import warnings

warnings.filterwarnings('ignore', category=DeprecationWarning)
import itertools
import os

os.environ['MKL_SERVICE_FORCE_INTEL'] = '1'
os.environ['MUJOCO_GL'] = 'egl'
os.environ['HYDRA_FULL_ERROR']='1'

import seaborn as sns; sns.set_theme()
from pathlib import Path
import torch.nn.functional as F
import hydra
import numpy as np
import torch
import wandb
from dm_env import specs
import pandas as pd
import dmc
import utils
from scipy.spatial.distance import cdist
from logger import Logger, save
from replay_buffer import ReplayBufferStorage, make_replay_loader, make_replay_buffer, ndim_grid, make_replay_offline
import matplotlib.pyplot as plt
from video import TrainVideoRecorder, VideoRecorder

torch.backends.cudnn.benchmark = True

from dmc_benchmark import PRIMAL_TASKS


def make_agent(obs_type, obs_spec, action_spec, goal_shape, num_expl_steps, cfg, lr=.0001, hidden_dim=1024, num_protos=512, update_gc=2, gc_only=False, offline=False, tau=.1, num_iterations=3, feature_dim=50, pred_dim=128, proj_dim=512, batch_size=1024, update_proto_every=10, lagr=.2, margin=.5, lagr1=.2, lagr2=.2, lagr3=.3):

    cfg.obs_type = obs_type
    cfg.obs_shape = obs_spec.shape
    cfg.action_shape = action_spec.shape
    cfg.num_expl_steps = num_expl_steps
    cfg.goal_shape = goal_shape
    cfg.lr = lr
    cfg.hidden_dim = hidden_dim
    cfg.num_protos=num_protos
    cfg.tau = tau

    if cfg.name.startswith('proto'):
        cfg.update_gc=update_gc
    cfg.offline=offline
    cfg.gc_only=gc_only
    cfg.batch_size = batch_size
    cfg.tau = tau
    cfg.num_iterations = num_iterations
    cfg.feature_dim = feature_dim
    cfg.pred_dim = pred_dim
    cfg.proj_dim = proj_dim
    cfg.lagr = lagr
    cfg.margin = margin
    if cfg.name=='protox':
        cfg.lagr1 = lagr1
        cfg.lagr2 = lagr2
        cfg.lagr3 = lagr3

    if cfg.name=='protov2':
        cfg.update_proto_every=update_proto_every
    return hydra.utils.instantiate(cfg)

def get_state_embeddings(agent, states):
    with torch.no_grad():
        s = agent.encoder(states)
        s = agent.predictor(s)
        s = agent.projector(s)
        s = F.normalize(s, dim=1, p=2)
    return s


def heatmaps(self, env, model_step, replay_dir2, goal,model_step_lb=False,gc=False,proto=False):
    
    if gc:

        heatmap = self.replay_storage1.state_visitation_gc

        plt.clf()
        fig, ax = plt.subplots(figsize=(10,6))
        sns.heatmap(np.log(1 + heatmap.T), cmap="Blues_r", cbar=False, ax=ax).invert_yaxis()
        ax.set_title(model_step)

        plt.savefig(f"./{model_step}_gc_heatmap.png")
        wandb.save(f"./{model_step}_gc_heatmap.png")


        heatmap_pct = self.replay_storage1.state_visitation_gc_pct

        plt.clf()
        fig, ax = plt.subplots(figsize=(10,10))
        labels = np.round(heatmap_pct.T/heatmap_pct.sum()*100, 1)
        sns.heatmap(np.log(1 + heatmap_pct.T), cmap="Blues_r", cbar=False, ax=ax).invert_yaxis()
        ax.set_title(model_step)

        plt.savefig(f"./{model_step}_gc_heatmap_pct.png")
        wandb.save(f"./{model_step}_gc_heatmap_pct.png")


        reward_matrix = self.replay_storage1.reward_matrix
        plt.clf()
        fig, ax = plt.subplots(figsize=(10,6))
        sns.heatmap(np.log(1 + reward_matrix.T), cmap="Blues_r", cbar=False, ax=ax).invert_yaxis()
        ax.set_title(model_step)

        plt.savefig(f"./{model_step}_gc_reward.png")
        wandb.save(f"./{model_step}_gc_reward.png")
        
        goal_matrix = self.replay_storage1.goal_state_matrix
        plt.clf()
        fig, ax = plt.subplots(figsize=(10,10))
        labels = np.round(goal_matrix.T/goal_matrix.sum()*100, 1)
        sns.heatmap(np.log(1 + goal_matrix.T), cmap="Blues_r", cbar=False, ax=ax).invert_yaxis()
        ax.set_title(model_step)

        plt.savefig(f"./{model_step}_goal_state_heatmap.png")
        wandb.save(f"./{model_step}_goal_state_heatmap.png")
        
    if proto:

        heatmap = self.replay_storage.state_visitation_proto

        plt.clf()
        fig, ax = plt.subplots(figsize=(10,6))
        sns.heatmap(np.log(1 + heatmap.T), cmap="Blues_r", cbar=False, ax=ax).invert_yaxis()
        ax.set_title(model_step)

        plt.savefig(f"./{model_step}_proto_heatmap.png")
        wandb.save(f"./{model_step}_proto_heatmap.png")


        heatmap_pct = self.replay_storage.state_visitation_proto_pct

        plt.clf()
        fig, ax = plt.subplots(figsize=(10,10))
        labels = np.round(heatmap_pct.T/heatmap_pct.sum()*100, 1)
        sns.heatmap(np.log(1 + heatmap_pct.T), cmap="Blues_r", cbar=False, ax=ax).invert_yaxis()
        ax.set_title(model_step)

        plt.savefig(f"./{model_step}_proto_heatmap_pct.png")
        wandb.save(f"./{model_step}_proto_heatmap_pct.png")
        
        

class Workspace:
    def __init__(self, cfg):
        self.work_dir = Path.cwd()
        print(f'workspace: {self.work_dir}')

        self.cfg = cfg
        utils.set_seed_everywhere(cfg.seed)
        self.device = torch.device(cfg.device)
        work_path = str(os.getcwd().split('/')[-2])+'/'+str(os.getcwd().split('/')[-1])

        # create logger
        if cfg.use_wandb:
            exp_name = '_'.join([
                cfg.experiment, cfg.agent.name, cfg.domain, cfg.obs_type,
                str(cfg.seed), str(cfg.tmux_session),work_path 
            ])
            wandb.init(project="urlb", group=cfg.agent.name, name=exp_name)

        self.logger = Logger(self.work_dir,
                             use_tb=cfg.use_tb,
                             use_wandb=cfg.use_wandb)
        # create envs
        task = self.cfg.task
        self.no_goal_task = self.cfg.task_no_goal
        idx = np.random.randint(0,400)
        goal_array = ndim_grid(2,20)
        self.first_goal = np.array([goal_array[idx][0], goal_array[idx][1]]) 
        self.train_env1 = dmc.make(self.cfg.task, cfg.obs_type, cfg.frame_stack,
                                   cfg.action_repeat, seed=None, goal=self.first_goal)
        print('goal', self.first_goal)
        self.train_env_no_goal = dmc.make(self.no_goal_task, cfg.obs_type, cfg.frame_stack,
                                   cfg.action_repeat, seed=None, goal=None)
        #import IPython as ipy; ipy.embed(colors='neutral')
        print('no goal task env', self.no_goal_task)
        self.train_env_goal = dmc.make(self.no_goal_task, 'states', cfg.frame_stack,
                                   1, seed=None, goal=None)
        self.train_env = dmc.make(self.no_goal_task, cfg.obs_type, cfg.frame_stack,
                                                  cfg.action_repeat, seed=None, goal=None)
        self.eval_env = dmc.make(self.cfg.task, cfg.obs_type, cfg.frame_stack,
                                 cfg.action_repeat, seed=None, goal=self.first_goal)
        self.eval_env_no_goal = dmc.make(self.no_goal_task, cfg.obs_type, cfg.frame_stack,
                                   cfg.action_repeat, seed=None, goal=None)
        self.eval_env_goal = dmc.make(self.no_goal_task, 'states', cfg.frame_stack,
                                   1, seed=None, goal=None)

        # create agent
        #import IPython as ipy; ipy.embed(colors='neutral')
        # create agent
        if self.cfg.agent.name=='protov2':
            self.agent = make_agent(cfg.obs_type,
                                self.train_env.observation_spec(),
                                self.train_env.action_spec(),
                                (3,84,84),
                                cfg.num_seed_frames // cfg.action_repeat,
                                cfg.agent,
                                cfg.lr,
                                cfg.hidden_dim,
                                cfg.num_protos,
                                cfg.update_gc,
                                False,
                                cfg.offline,
                                cfg.tau,
                                cfg.num_iterations,
                                cfg.feature_dim,
                                cfg.pred_dim,
                                cfg.proj_dim,
                                batch_size=cfg.batch_size,
                                update_proto_every=cfg.update_proto_every)
        
        elif self.cfg.agent.name=='protox':
            self.agent = make_agent(cfg.obs_type,
                                self.train_env.observation_spec(),
                                self.train_env.action_spec(),
                                (3,84,84),
                                cfg.num_seed_frames // cfg.action_repeat,
                                cfg.agent,
                                cfg.lr,
                                cfg.hidden_dim,
                                cfg.num_protos,
                                cfg.update_gc,
                                False,
                                cfg.offline,
                                cfg.tau,
                                cfg.num_iterations,
                                cfg.feature_dim,
                                cfg.pred_dim,
                                cfg.proj_dim,
                                batch_size=cfg.batch_size,
                                lagr1=cfg.lagr1,
                                lagr2=cfg.lagr2,
                                lagr3=cfg.lagr3,
                                margin=cfg.margin) 
        else: 
            self.agent = make_agent(cfg.obs_type,
                                self.train_env.observation_spec(),
                                self.train_env.action_spec(),
                                (3,84,84),
                                cfg.num_seed_frames // cfg.action_repeat,
                                cfg.agent,
                                cfg.lr,
                                cfg.hidden_dim,
                                cfg.num_protos,
                                cfg.update_gc,
                                False,
                                cfg.offline,
                                cfg.tau,
                                cfg.num_iterations,
                                cfg.feature_dim,
                                cfg.pred_dim,
                                cfg.proj_dim,
                                batch_size=cfg.batch_size,
                                lagr=cfg.lagr,
                                margin=cfg.margin)
            
        # get meta specs
        meta_specs = self.agent.get_meta_specs()
        # create replay buffer
        data_specs = (self.train_env.observation_spec(),
                      self.train_env.action_spec(),
                      specs.Array((1,), np.float32, 'reward'),
                      specs.Array((1,), np.float32, 'discount'))

        # create data storage
        self.replay_storage1 = ReplayBufferStorage(data_specs, meta_specs,
                                                  self.work_dir / 'buffer1')
        self.replay_storage = ReplayBufferStorage(data_specs, meta_specs,
                                                  self.work_dir / 'buffer2')
        

        # create replay buffer
        print('regular or hybrid_gc loader')
        self.replay_loader1 = make_replay_loader(self.replay_storage1,
                                                    False,
                                                    cfg.replay_buffer_gc,
                                                    cfg.batch_size_gc,
                                                    cfg.replay_buffer_num_workers,
                                                    False, cfg.nstep, cfg.discount,
                                                    True, cfg.hybrid_gc,cfg.obs_type,
                                                    cfg.hybrid_pct)

        self.replay_loader = make_replay_loader(self.replay_storage,
                                                False,
                                                cfg.replay_buffer_size,
                                                cfg.batch_size,
                                                cfg.replay_buffer_num_workers,
                                                False, cfg.nstep, cfg.discount,
                                                goal=False,
                                                obs_type=cfg.obs_type,
                                                loss=cfg.loss,
                                                test=cfg.test) 

        self._replay_iter = None
        self._replay_iter1 = None

        self.video_recorder = VideoRecorder(
            self.work_dir if cfg.save_video else None,
            camera_id=0 if 'quadruped' not in self.cfg.domain else 2,
            use_wandb=self.cfg.use_wandb)
        self.train_video_recorder = TrainVideoRecorder(
            self.work_dir if cfg.save_train_video else None,
            camera_id=0 if 'quadruped' not in self.cfg.domain else 2,
            use_wandb=self.cfg.use_wandb)

        self.timer = utils.Timer()
        self._global_step = 0
        self._global_episode = 0
        self.unreachable_goal = np.empty((0,9,84,84))
        self.unreachable_state = np.empty((0,2))
        self.loaded = False
        self.loaded_uniform = False
        self.uniform_goal = []
        self.uniform_state = []
        self.count_uniform = 0
        self.goal_loaded = False
        self.distance_goal = []
        self.count=0
        self.global_success_rate = []
        self.global_index=[]
        self.storage1=False
        self.proto_goal = []
        self.distance_goal_init = {}
        self.proto_goals = np.empty((self.cfg.num_protos, 2))
        self.actor=True
        self.actor1=False
        self.final_df = pd.DataFrame(columns=['avg', 'med', 'max', 'q7', 'q8', 'q9'])

    @property
    def global_step(self):
        return self._global_step

    @property
    def global_episode(self):
        return self._global_episode

    @property
    def global_frame(self):
        return self.global_step * self.cfg.action_repeat

    @property
    def replay_iter1(self):
        if self._replay_iter1 is None:
            self._replay_iter1 = iter(self.replay_loader1)
        return self._replay_iter1

    @property
    def replay_iter(self):
        if self._replay_iter is None:
            self._replay_iter = iter(self.replay_loader)
        return self._replay_iter
    

    def sample_goal_distance(self, init_state=None):
        if self.goal_loaded==False:
            goal_array = ndim_grid(2,20)
            if init_state is None:
                dist_goal = cdist(np.array([[-.15,.15]]), goal_array, 'euclidean')
            else:
                dist_goal = cdist(np.array([[init_state[0],init_state[1]]]), goal_array, 'euclidean')
                
                
            df1 = pd.DataFrame()
            df1['distance'] = dist_goal.reshape((400,))
            df1['index'] = df1.index
            df1 = df1.sort_values(by='distance')
            goal_array_ = []
            for x in range(len(df1)):
                goal_array_.append(goal_array[df1.iloc[x,1]])
            self.distance_goal = goal_array_
            self.goal_loaded=True
            index=self.global_step//1000
            idx = np.random.randint(index,min(index+30, 400))

        else:
            if self.global_step<500000:
                index=self.global_step//1000
                if index<400:
                    idx = np.random.randint(index,min(index+30, 400))
                else:
                    idx = np.random.randint(0,400)
            else:
                idx = np.random.randint(0,400)

        return self.distance_goal[idx]
    
    def eval_proto(self):
        heatmaps(self, self.eval_env, self.global_step, False, True, model_step_lb=False,gc=True,proto=True)
        eval_env_goal = dmc.make('point_mass_maze_reach_no_goal', 'pixels', 3, 2, seed=None, goal=None)


        protos = self.agent.protos.weight.data.detach().clone()

        replay_buffer = make_replay_offline(eval_env_goal,
                                                self.work_dir / 'buffer2' / 'buffer_copy',
                                                500000,
                                                0,
                                                0,
                                                .99,
                                                goal=False,
                                                relabel=False,
                                                model_step = self._global_step,
                                                replay_dir2=False,
                                                obs_type = 'pixels'
                                                )

        state, actions, rewards, eps, index = replay_buffer.parse_dataset() 
        state = state.reshape((state.shape[0],4))

        num_sample=600 
        idx = np.random.randint(0, state.shape[0], size=num_sample)
        state=state[idx]
        state=state.reshape(num_sample,4)
        a = state
        count10,count01,count00,count11=(0,0,0,0)
        # density estimate:
        df = pd.DataFrame()
        for state_ in a:
            if state_[0]<0:
                if state_[1]>=0:
                    count10+=1
                else:
                    count00+=1
            else:
                if state_[1]>=0:
                    count11+=1
                else:
                    count01+=1

        df.loc[0,0] = count00/a.shape[0]
        df.loc[0,1] = count01/a.shape[0]
        df.loc[1,1] = count11/a.shape[0]
        df.loc[1,0] = count10/a.shape[0]
        labels=df
        plt.clf()
        fig, ax = plt.subplots()
        sns.heatmap(df, cmap="Blues_r",cbar=False, annot=labels).invert_yaxis()
        ax.set_title('data percentage')
        plt.savefig(self.work_dir / f"data_pct_model_{self._global_step}.png")

        def ndim_grid(ndims, space):
            L = [np.linspace(-.25,.25,space) for i in range(ndims)]
            return np.hstack((np.meshgrid(*L))).swapaxes(0,1).reshape(ndims,-1).T

        lst=[]
        goal_array = ndim_grid(2,10)
        for ix,x in enumerate(goal_array):
            if (-.2<x[0]<.2 and -.02<x[1]<.02) or (-.02<x[0]<.02 and -.2<x[1]<.2):
                lst.append(ix)


        goal_array=np.delete(goal_array, lst,0)
        emp = np.zeros((goal_array.shape[0],2))
        goal_array = np.concatenate((goal_array, emp), axis=1)


        #pixels = []
        encoded = []
        proto = []
        actual_proto = []
        lst_proto = []

        for x in idx:
            fn = eps[x]
            idx_ = index[x]
            ep = np.load(fn)
            #pixels.append(ep['observation'][idx_])

            with torch.no_grad():
                obs = ep['observation'][idx_]
                obs = torch.as_tensor(obs.copy(), device=self.device).unsqueeze(0)
                z = self.agent.encoder(obs)
                encoded.append(z)
                z = self.agent.predictor(z)
                z = self.agent.projector(z)
                z = F.normalize(z, dim=1, p=2)
                proto.append(z)
                sim = self.agent.protos(z)
                idx_ = sim.argmax()
                actual_proto.append(protos[idx_][None,:])

        encoded = torch.cat(encoded,axis=0)
        proto = torch.cat(proto,axis=0)
        actual_proto = torch.cat(actual_proto,axis=0)

        proto_dist = torch.norm(protos[:,None,:] - proto[None,:, :], dim=2, p=2)
        all_dists_proto, _proto = torch.topk(proto_dist, 10, dim=1, largest=False)
        #import IPython as ipy; ipy.embed(colors='neutral')
        self.proto_goals = a[_proto[:,0].clone().detach().cpu().numpy(), :2]
        print('proto_goals', self.proto_goals)
        
        # retrieve closest states after projecting prototypes down 
        # use same batch to calculate q values? & use knn to see which prototype has highest intrinsic reward?
        #

        with torch.no_grad():
            #proto_sim = self.agent.protos(proto).T
            proto_sim = torch.exp(-1/2*torch.square(torch.norm(protos[:,None,:] - proto[None,:, :], dim=2, p=2)))
        all_dists_proto_sim, _proto_sim = torch.topk(proto_sim, 10, dim=1, largest=True)

        proto_self = torch.norm(protos[:,None,:] - protos[None,:, :], dim=2, p=2)
        all_dists_proto_self, _proto_self = torch.topk(proto_self, protos.shape[0], dim=1, largest=False)

        with torch.no_grad():
            proto_sim_self = self.agent.protos(protos).T
        all_dists_proto_sim_self, _proto_sim_self = torch.topk(proto_sim_self, protos.shape[0], dim=1, largest=True)
        
        dist_matrices = [_proto, _proto_sim]
        self_mat = [_proto_self, _proto_sim_self]
        names = [self.work_dir / f"{self.global_step}_prototypes.gif", self.work_dir / f"{self.global_step}_prototypes_sim.gif"]

        for index_, dist_matrix in enumerate(dist_matrices):
            filenames=[]
            order = self_mat[index_][0,:].cpu().numpy()
            plt.clf()
            fig, ax = plt.subplots()
            dist_np = np.empty((_proto_self.shape[1], dist_matrix.shape[1], 2))
            
            for ix, x in enumerate(order):
                txt=''
                df = pd.DataFrame()
                count=0
                for i in range(a.shape[0]+1):
                    if i!=a.shape[0]:
                        df.loc[i,'x'] = a[i,0]
                        df.loc[i,'y'] = a[i,1]
                        df.loc[i,'distance_to_proto1'] = _proto_self[ix,0].item()
                        
                        if i in dist_matrix[ix,:]:
                            df.loc[i, 'c'] = str(ix+1)
                            dist_np[ix,count,0] = a[i,0]
                            dist_np[ix,count,1] = a[i,1]
                            
                            count+=1
                            #z=dist_matrix[ix,(dist_matrix[ix,:] == i).nonzero(as_tuple=True)[0]]
                            #txt += ' ['+str(np.round(state[z][0],2))+','+str(np.round(state[z][1],2))+'] '
                        elif ix==0 and (i not in dist_matrix[ix,:]):
                            #color all samples blue
                            df.loc[i,'c'] = str(0)

                #order based on distance to first prototype
                #plt.clf()
                palette = {
                           	    '0': 'tab:blue',
                                    '1': 'tab:orange',
                                    '2': 'black',
                                    '3':'silver',
                                    '4':'green',
                                    '5':'red',
                                    '6':'purple',
                                    '7':'brown',
                                    '8':'pink',
                                    '9':'gray',
                                    '10':'olive',
                                    '11':'cyan',
                                    '12':'yellow',
                                    '13':'skyblue',
                                    '14':'magenta',
                                    '15':'lightgreen',
                                    '16':'blue',
                                    '17':'lightcoral',
                                    '18':'maroon',
                                    '19':'saddlebrown',
                                    '20':'peru',
                                    '21':'tan',
                                    '22':'darkkhaki',
                                    '23':'darkolivegreen',
                                    '24':'mediumaquamarine',
                                    '25':'lightseagreen',
                                    '26':'paleturquoise',
                                    '27':'cadetblue',
                                    '28':'steelblue',
                                    '29':'thistle',
                                    '30':'slateblue',
                                    '31':'hotpink',
                                    '32':'papayawhip'
                        }
                #fig, ax = plt.subplots()
                ax=sns.scatterplot(x="x", y="y",
                          hue="c",palette=palette,
                          data=df,legend=True)
                sns.move_legend(ax, "upper left", bbox_to_anchor=(1, 1))
                #ax.set_title("\n".join(wrap(txt,75)))
            
            pairwise_dist = np.linalg.norm(dist_np[:,0,None,:]-dist_np, ord=2, axis=2)
            
            #maximum pairwise distance amongst prototypes
            maximum = np.amax(pairwise_dist, axis=1)
            num = self.global_step//self.cfg.eval_every_frames
            self.final_df.loc[num, 'avg'] = np.mean(maximum)
            self.final_df.loc[num, 'med'] = np.median(maximum)
            self.final_df.loc[num, 'q9'] = np.quantile(maximum, .9)
            self.final_df.loc[num, 'q8'] = np.quantile(maximum, .8)
            self.final_df.loc[num, 'q7'] = np.quantile(maximum, .7)
            self.final_df.loc[num, 'max'] = np.max(maximum)
            


            if index_==0:
                file1= self.work_dir / f"10nn_actual_prototypes_{self.global_step}.png"
                plt.savefig(file1)
                wandb.save(f"10nn_actual_prototypes_{self.global_step}.png")
            elif index_==1:
                file1= self.work_dir / f"10nn_actual_prototypes_sim_{self.global_step}.png"
                plt.savefig(file1)
                wandb.save(f"10nn_actual_prototypes_sim_{self.global_step}.png")

            #import IPython as ipy; ipy.embed(colors='neutral')
            if self.global_step >= (self.cfg.num_train_frames//2-100):
                fig, ax = plt.subplots()
                self.final_df.plot(ax=ax)
                ax.set_xticks(self.final_df.index)
                plt.savefig(self.work_dir / f"proto_states.png")
                wandb.save(f"proto_states.png")

    def eval(self):
        #self.encode_proto(heatmap_only=True) 
        heatmaps(self, self.eval_env, self.global_step, False, True, model_step_lb=False,gc=True,proto=False)
        goal_array = self.proto_goals
        success=0
        df = pd.DataFrame(columns=['x','y','r'], dtype=np.float64) 

        for ix, x in enumerate(goal_array):
            dist_goal = cdist(np.array([x]), goal_array, 'euclidean')
            df1=pd.DataFrame()
            df1['distance'] = dist_goal.reshape((goal_array.shape[0],))
            df1['index'] = df1.index
            df1 = df1.sort_values(by='distance')
            success=0
            step, episode, total_reward = 0, 0, 0
            #goal_pix, goal_state = self.sample_goal_uniform(eval=True)
            goal_state = np.array([x[0], x[1]])
            self.eval_env = dmc.make(self.cfg.task, self.cfg.obs_type, self.cfg.frame_stack,
                    self.cfg.action_repeat, seed=None, goal=goal_state)
            self.eval_env_goal = dmc.make(self.no_goal_task, 'states', self.cfg.frame_stack,
                    self.cfg.action_repeat, seed=None, goal=None)
            eval_until_episode = utils.Until(self.cfg.num_eval_episodes)
            meta = self.agent.init_meta()

            while eval_until_episode(episode):
                time_step = self.eval_env.reset()
                self.eval_env_no_goal = dmc.make(self.no_goal_task, self.cfg.obs_type, self.cfg.frame_stack,
                	self.cfg.action_repeat, seed=None, goal=None, init_state=time_step.observation['observations'][:2]) 
                time_step_no_goal = self.eval_env_no_goal.reset()

                with self.eval_env_goal.physics.reset_context():
                    time_step_goal = self.eval_env_goal.physics.set_state(np.array([goal_state[0], goal_state[1], 0, 0]))
                time_step_goal = self.eval_env_goal._env.physics.render(height=84, width=84, camera_id=dict(quadruped=2).get('point_mass_maze', 0))
                self.video_recorder.init(self.eval_env, enabled=(episode == 0))
         
                while step!=self.cfg.episode_length:
                    with torch.no_grad(), utils.eval_mode(self.agent):
                        if self.cfg.goal:
                            action = self.agent.act(time_step_no_goal.observation['pixels'],
                                                time_step_goal,
                                                meta,
                                                self._global_step,
                                                eval_mode=True)
                        else:
                            action = self.agent.act(time_step.observation,
                                                meta,
                                                self._global_step,
                                                eval_mode=True)
                    time_step = self.eval_env.step(action)
                    time_step_no_goal = self.eval_env_no_goal.step(action)
                    #time_step_goal = self.eval_env_goal.step(action)
                    self.video_recorder.record(self.eval_env)
                    total_reward += time_step.reward
                    step += 1

                episode += 1
                
            
                if ix%10==0:
                    self.video_recorder.save(f'{self.global_frame}_{ix}.mp4')

                if self.cfg.eval:
                    save(str(self.work_dir)+'/eval_{}.csv'.format(model.split('.')[-2].split('/')[-1]), [[x.cpu().detach().numpy(), total_reward, time_step.observation[:2], step]])

                else:
                        save(str(self.work_dir)+'/eval_{}.csv'.format(self._global_step), [[goal_state, total_reward, time_step.observation['observations'], step]])
            
                if total_reward > 20*self.cfg.num_eval_episodes:
                    success+=1
            
            df.loc[ix, 'x'] = x[0]
            df.loc[ix, 'y'] = x[1]
            df.loc[ix, 'r'] = total_reward
            print('r', total_reward)

        result = df.groupby(['x', 'y'], as_index=True).max().unstack('x')['r']/2
        plt.clf()
        fig, ax = plt.subplots()
        sns.heatmap(result, cmap="Blues_r").invert_yaxis()
        plt.savefig(f"./{self.global_step}_heatmap_goal.png")
        wandb.save(f"./{self.global_step}_heatmap_goal.png")
            

    def eval_intrinsic(self, model):
        obs = torch.empty(1024, 9, 84, 84)
        states = torch.empty(1024, 4)
        grid_embeddings = torch.empty(1024, 128)
        actions = torch.empty(1024,2)
        meta = self.agent.init_meta()
        for i in range(1024):
            with torch.no_grad():
                grid, state = self.encoding_grid()
                action = self.agent.act2(grid, meta, self._global_step, eval_mode=True)
                actions[i] = action
                obs[i] = grid
                states[i] = torch.tensor(state).cuda().float()
        import IPython as ipy; ipy.embed(colors='neutral')    
        obs = obs.cuda().float()
        actions = actions.cuda().float()
        grid_embeddings = get_state_embeddings(self.agent, obs)
        protos = self.agent.protos.weight.data.detach().clone()
        protos = F.normalize(protos, dim=1, p=2)
        dist_mat = torch.cdist(protos, grid_embeddings)
        closest_points = dist_mat.argmin(-1)
        proto2d = states[closest_points.cpu(), :2]
        with torch.no_grad():
            reward = self.agent.compute_intr_reward(obs, self._global_step)
            q_value = self.agent.get_q_value(obs, actions)
        for x in range(len(reward)):
            print('saving')
            print(str(self.work_dir)+'/eval_intr_reward_{}.csv'.format(self._global_step))
            save(str(self.work_dir)+'/eval_intr_reward_{}.csv'.format(self._global_step), [[obs[x].cpu().detach().numpy(), reward[x].cpu().detach().numpy(), q[x].cpu().detach().numpy(), self._global_step]])

        
    def train(self):
        # predicates
        train_until_step = utils.Until(self.cfg.num_train_frames,
                                       self.cfg.action_repeat)
        seed_until_step = utils.Until(self.cfg.num_seed_frames,
                                      self.cfg.action_repeat)
        eval_every_step = utils.Every(self.cfg.eval_every_frames,
                                      self.cfg.action_repeat)

        episode_step, episode_reward = 0, 0
        
        time_step = self.train_env.reset()
        meta = self.agent.init_meta() 
         
        if self.cfg.obs_type == 'pixels':
            self.replay_storage.add(time_step, meta, True)  
            print('replay2')
        else:
            self.replay_storage.add(time_step, meta)  

        metrics = None

        while train_until_step(self.global_step):
            #test
            if self.global_step < self.cfg.switch_gc:
                if time_step.last():
                    self._global_episode += 1
                    # wait until all the metrics schema is populated
                    if metrics is not None:
                        # log stats
                        elapsed_time, total_time = self.timer.reset()
                        episode_frame = episode_step * self.cfg.action_repeat
                        with self.logger.log_and_dump_ctx(self.global_frame,
                                                          ty='train') as log:
                            log('fps', episode_frame / elapsed_time)
                            log('total_time', total_time)
                            log('episode_reward', episode_reward)
                            log('episode_length', episode_frame)
                            log('episode', self.global_episode)
                            log('buffer_size', len(self.replay_storage))
                            log('step', self.global_step)

                    # reset env
                    if self.cfg.const_init==False:
                        task = PRIMAL_TASKS[self.cfg.domain]
                        rand_init = np.random.uniform(.02,.29,size=(2,))
                        sign = np.array([[1,1],[-1,1],[1,-1],[-1,-1]])
                        rand = np.random.randint(4)
                        self.train_env = dmc.make(self.cfg.task_no_goal, self.cfg.obs_type, self.cfg.frame_stack,
                                                                  self.cfg.action_repeat, self.cfg.seed, init_state=(rand_init[0]*sign[rand][0], rand_init[1]*sign[rand][1]))
                        print('sampled init', (rand_init[0]*sign[rand][0], rand_init[1]*sign[rand][1]))   
                    time_step = self.train_env.reset()
                    meta = self.agent.init_meta()
                    if self.cfg.obs_type=='pixels':

                        self.replay_storage.add(time_step, meta, True)
                    else: 
                        self.replay_storage.add(time_step, meta)
                    # try to save snapshot
                    if self.global_frame in self.cfg.snapshots:
                        self.save_snapshot()
                    episode_step = 0
                    episode_reward = 0

                # try to evaluate
                if eval_every_step(self.global_step) and self.global_step!=0:
                    self.logger.log('eval_total_time', self.timer.total_time(),
                                    self.global_frame)
                    if self.cfg.debug:
                        self.eval()
                    elif self.cfg.agent.name=='protov2':
                        self.eval_protov2()
                    else:
                        self.eval_proto()

                meta = self.agent.update_meta(meta, self.global_step, time_step)
                # sample action
                with torch.no_grad(), utils.eval_mode(self.agent):
                    if self.cfg.obs_type=='pixels':
                        if self.cfg.use_predictor:
                            action = self.agent.act2(time_step.observation['pixels'],
                                            meta,
                                            self.global_step,
                                            eval_mode=True,
                                            proto=self.agent)
                        else:
                            action = self.agent.act2(time_step.observation['pixels'],
                                            meta,
                                            self.global_step,
                                            eval_mode=True) 
                    else:    
                        action = self.agent.act2(time_step.observation,
                                            meta,
                                            self.global_step,
                                            eval_mode=True)
                # try to update the agent
                if not seed_until_step(self.global_step):
                    metrics = self.agent.update(self.replay_iter, self.global_step, test=self.cfg.test)
                    self.logger.log_metrics(metrics, self.global_frame, ty='train')

                #save agent
                if self._global_step%200000==0 and self._global_step!=0:
                    path = os.path.join(self.work_dir, 'optimizer_{}_{}.pth'.format(str(self.cfg.agent.name),self._global_step))
                    torch.save(self.agent, path)

                # take env step
                time_step = self.train_env.step(action)
                episode_reward += time_step.reward
                if  self.cfg.obs_type=='pixels':
                    self.replay_storage.add(time_step, meta, True)
                else:
                    self.replay_storage.add(time_step, meta)
                episode_step += 1
                self._global_step += 1
               
            #switching between gc & proto
            
            else:
                if self.global_step==self.cfg.switch_gc:
                    self.actor1=True
                    self.actor=False 

                    time_step1 = self.train_env1.reset()
                    self.train_env_no_goal = dmc.make(self.no_goal_task, self.cfg.obs_type, self.cfg.frame_stack,
                    self.cfg.action_repeat, seed=None, goal=self.first_goal, init_state=time_step1.observation['observations'][:2])
                    time_step_no_goal = self.train_env_no_goal.reset()
                    time_step_goal = self.train_env_goal.reset()
                    with self.train_env_goal.physics.reset_context():
                        time_step_goal = self.train_env_goal.physics.set_state(np.array([self.first_goal[0], self.first_goal[1], 0, 0]))

                    time_step_goal = self.train_env_goal._env.physics.render(height=84, width=84, camera_id=dict(quadruped=2).get('point_mass_maze', 0))

                    meta = self.agent.init_meta()

                    if self.cfg.obs_type == 'pixels':
                        self.replay_storage1.add_goal(time_step1, meta, time_step_goal, time_step_no_goal,self.train_env_goal.physics.state(), True)
                        print('replay1')
 
                
                if ((time_step1.last() and self.actor1) or (time_step.last() and self.actor) or episode_step==self.cfg.episode_length) and self.global_step!=self.cfg.switch_gc:
                    print('last')
                    self._global_episode += 1
                    # wait until all the metrics schema is populated
                    if metrics is not None:
                        # log stats
                        elapsed_time, total_time = self.timer.reset()
                        episode_frame = episode_step * self.cfg.action_repeat
                        with self.logger.log_and_dump_ctx(self.global_frame,ty='train') as log:
                            log('fps', episode_frame / elapsed_time)
                            log('total_time', total_time)
                            log('episode_reward', episode_reward)
                            log('episode_length', episode_frame)
                            log('episode', self.global_episode)
                            log('buffer_size', len(self.replay_storage1))
                            log('step', self.global_step)

                    if self.cfg.obs_type =='pixels' and self.actor1:
                        self.replay_storage1.add_goal(time_step1, meta,time_step_goal, time_step_no_goal, self.train_env_goal.physics.state(), True, last=True)
                    elif self.cfg.obs_type =='pixels' and self.actor:
                        self.replay_storage.add(time_step, meta, True)
                    else:
                        self.replay_storage.add(time_step, meta)

                    # try to save snapshot
                    if self.global_frame in self.cfg.snapshots:
                        self.save_snapshot()
                    episode_step = 0
                    episode_reward = 0
                    self.actor1=True
                    self.actor=False


                # try to evaluate
                if eval_every_step(self.global_step) and self.global_step!=0:
                    #print('trying to evaluate')
                    self.eval()
                    self.eval_proto()

                if episode_step== 0 and self.global_step!=0:
                    
                    self.recorded=False               

                    goal_array = self.proto_goals
                    idx = np.random.randint(0, goal_array.shape[0])
                    goal_state = np.array([goal_array[idx][0], goal_array[idx][1]])


                    self.train_env1 = dmc.make(self.cfg.task, self.cfg.obs_type, 
                                               self.cfg.frame_stack,self.cfg.action_repeat, 
                                               seed=None, goal=goal_state)
                    
                    time_step1 = self.train_env1.reset()
                    self.train_env_no_goal = dmc.make(self.no_goal_task, self.cfg.obs_type, self.cfg.frame_stack,
                    self.cfg.action_repeat, seed=None, goal=goal_state, init_state=time_step1.observation['observations'][:2])
                    time_step_no_goal = self.train_env_no_goal.reset()
                    meta = self.agent.update_meta(meta, self._global_step, time_step1) 
                    print('time step', time_step1.observation['observations'])
                    print('sampled goal', goal_state)

                    with self.train_env_goal.physics.reset_context():
                        time_step_goal = self.train_env_goal.physics.set_state(np.array([goal_state[0], goal_state[1],0,0]))

                    time_step_goal = self.train_env_goal._env.physics.render(height=84, width=84, camera_id=dict(quadruped=2).get('point_mass_maze', 0))

                    if self.cfg.obs_type == 'pixels' and time_step1.last()==False and episode_step!=self.cfg.episode_length:
                        self.replay_storage1.add_goal(time_step1, meta,time_step_goal, time_step_no_goal,self.train_env_goal.physics.state(), True)
                
                # sample action
                if self.actor1:
                    with torch.no_grad(), utils.eval_mode(self.agent):
                        if self.cfg.obs_type == 'pixels':

                            action1 = self.agent.act(time_step_no_goal.observation['pixels'].copy(),
                                                    time_step_goal.copy(),
                                                    meta,
                                                    self._global_step,
                                                    eval_mode=False)
                        else:
                            action = self.agent.act(time_step.observation,
                                                meta,
                                                self.global_step,
                                                eval_mode=False)

                    # take env step
                    time_step1 = self.train_env1.step(action1)
                    time_step_no_goal = self.train_env_no_goal.step(action1)
                    episode_reward += time_step1.reward


                    if self.cfg.obs_type == 'pixels' and time_step1.last()==False and episode_step!=self.cfg.episode_length and self.cfg.resample_goal==False:
                        self.replay_storage1.add_goal(time_step1, meta, time_step_goal, time_step_no_goal,self.train_env_goal.physics.state(), True)
                    elif self.cfg.obs_type == 'pixels' and time_step1.last()==False and episode_step!=self.cfg.episode_length and self.cfg.resample_goal and episode_reward<=100:
                        self.replay_storage1.add_goal(time_step1, meta, time_step_goal, time_step_no_goal,self.train_env_goal.physics.state(), True)
                    elif self.cfg.obs_type == 'states':
                        self.replay_storage1.add_goal(time_step1, meta, goal)
                
                else:
                    
                    with torch.no_grad(), utils.eval_mode(self.agent):
                        if self.cfg.obs_type=='pixels':
                            if self.cfg.use_predictor:
                                action = self.agent.act2(time_step.observation['pixels'],
                                                meta,
                                                self.global_step,
                                                eval_mode=True,
                                                proto=self.agent)
                            else:
                                action = self.agent.act2(time_step.observation['pixels'],
                                                meta,
                                                self.global_step,
                                                eval_mode=True) 
                        else:    
                            action = self.agent.act2(time_step.observation,
                                                goal,
                                                meta,
                                                self.global_step,
                                                eval_mode=True)
                            
                    time_step = self.train_env.step(action)
                    episode_reward += time_step.reward
                    if  self.cfg.obs_type=='pixels':
                        self.replay_storage.add(time_step, meta, True)
                    else:
                        self.replay_storage.add(time_step, meta)

                episode_step += 1

                if episode_reward > 100 and episode_step<490 and self.actor1:
                    print('reached start exploring')
                    self.actor=True
                    self.actor1=False
                    if self.cfg.obs_type == 'pixels' and time_step1.last()==False:
                        self.replay_storage1.add_goal(time_step1, meta,time_step_goal, time_step_no_goal,self.train_env_goal.physics.state(), True, last=True)
                        
                    episode_reward=0
                    current_state = time_step1.observation['observations'][:2]
                    print('current_state', current_state)
                    self.train_env = dmc.make(self.cfg.task_no_goal, self.cfg.obs_type, self.cfg.frame_stack,
                                              self.cfg.action_repeat, seed=None, goal=goal_state, 
                                              init_state=(current_state[0], current_state[1]))
                    #reset so the first part doesn't try to save episode for time_step1.last() 
                    time_step1 = self.train_env1.reset()
                    print('should reset to', current_state)
                    print('new env state', self.train_env._env.physics.state())
                    time_step = self.train_env.reset()
                    print('reset state', time_step.observation['observations'])
                    meta = self.agent.update_meta(meta, self._global_step, time_step)

                    if self.cfg.obs_type == 'pixels' and time_step.last()==False:
                        self.replay_storage.add(time_step, meta, True, last=False)

                if self.global_step> (self.cfg.switch_gc+self.cfg.num_seed_frames) and self.actor1:

                    metrics = self.agent.update(self.replay_iter1, self.global_step, actor1=True)
                    self.logger.log_metrics(metrics, self.global_frame, ty='train')
                    
                elif not seed_until_step(self.global_step) and self.actor:
                    
                    metrics = self.agent.update(self.replay_iter, self.global_step, test=self.cfg.test)
                    self.logger.log_metrics(metrics, self.global_frame, ty='train')

                self._global_step += 1
            

            if self._global_step%100000==0 and self._global_step!=0:
                print('saving agent')
                path = os.path.join(self.work_dir, 'optimizer_{}_{}.pth'.format(str(self.cfg.agent.name),self._global_step))
                torch.save(self.agent, path)
                path_2 = os.path.join(self.work_dir, 'encoder_{}_{}.pth'.format(str(self.cfg.agent.name),self._global_step))
                torch.save(self.agent.encoder, path_2)

    def save_snapshot(self):
        snapshot_dir = self.work_dir / Path(self.cfg.snapshot_dir)
        snapshot_dir.mkdir(exist_ok=True, parents=True)
        snapshot = snapshot_dir / f'snapshot_{self.global_frame}.pt'
        keys_to_save = ['agent', '_global_step', '_global_episode']
        payload = {k: self.__dict__[k] for k in keys_to_save}
        with snapshot.open('wb') as f:
            torch.save(payload, f)


@hydra.main(config_path='.', config_name='pretrain')
def main(cfg):
    from pph import Workspace as W
    root_dir = Path.cwd()
    workspace = W(cfg)
    snapshot = root_dir / 'snapshot.pt'
    if snapshot.exists():
        print(f'resuming: {snapshot}')
        workspace.load_snapshot()
    workspace.train()


if __name__ == '__main__':
    main()