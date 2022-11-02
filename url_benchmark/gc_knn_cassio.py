import scipy
import glob
import seaborn as sns
import pandas as pd
import re
import natsort
import random
from scipy.spatial.distance import cdist
import matplotlib.pyplot as plt
import imageio
import warnings
from textwrap import wrap

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
import io
import itertools

torch.backends.cudnn.benchmark = True

from dmc_benchmark import PRIMAL_TASKS

models = ['/misc/vlgscratch4/FergusGroup/mortensen/proto_explore/url_benchmark/exp_local/2022.10.16/191115_proto_encoder1/']

def act(obs, goal, step, eval_mode):
    with torch.no_grad():
        #obs = torch.as_tensor(obs, device='cuda').unsqueeze(0)
        #h = encoder.encoder(obs)
        #g = encoder.encoder(goal)
        inputs = [obs]
        inputs2 = goal
        inpt = torch.cat(inputs, dim=-1)
        #assert obs.shape[-1] == self.obs_shape[-1]
        if ac:
            dist = actor(inpt, inputs2, .2)
        else:
            dist = agent.actor(inpt, inputs2, .2)
        action = dist.mean

        return action

for m in models:
    model = m.split('/')[-3] + '_' +m.split('/')[-2]
    tmp_agent_name = m.split('/')[-2].split('_')
    print(tmp_agent_name)
    agent_name = tmp_agent_name[-2] + '_' + tmp_agent_name[-1]
    agents = glob.glob(m+'o*000000.pth')
    if len(agents)==0:
        ac=True
        actor = glob.glob(m+'a*000000.pth')
        print(actor)
        critic = glob.glob(m+'c*000000.pth')
        agents = list(list(x) for x in zip(actor, critic))
    else:
        ac=False
    for i, path in enumerate(agents):
        if ac:
            model_step = actor[i].split('_')[-1].split('.')[0]
        else:
            model_step = path.split('_')[-1].split('.')[0]
        print('model', m)
        print('model step', model_step)
        if model_step=='0':
            continue
        print(path)
        if ac:
            actor  = torch.load(path[0],map_location='cuda')
            critic = torch.load(path[1],map_location='cuda')
            encoder = torch.load('/misc/vlgscratch4/FergusGroup/mortensen/proto_explore/url_benchmark/models/2022.10.14/210339_proto_encoder1_lambda/optimizer_proto_encoder1_1000000.pth')
        else:
            agent  = torch.load(path,map_location='cuda')
        eval_env_goal = dmc.make('point_mass_maze_reach_no_goal', 'pixels', 3, 2, seed=None, goal=None)
        env = dmc.make('point_mass_maze_reach_no_goal', 'pixels', 3, 2, seed=None, goal=None)
        
        
        if model == '2022.09.09_072830_proto':
            replay_dir = Path(m+'buffer2/buffer_copy/')
        else:
            replay_dir = Path(m+'buffer1/buffer_copy/')

        replay_buffer = make_replay_offline(eval_env_goal,
                                                replay_dir,
                                                100000,
                                                0,
                                                0,
                                                .99,
                                                goal=False,
                                                relabel=False,
                                                model_step = model_step,
                                                replay_dir2=False,
                                                obs_type = 'pixels'
                                                )

        state, actions, rewards, eps, index = replay_buffer.parse_dataset() 
        state = state.reshape((state.shape[0],4))
        print(state.shape)
#         num_sample=10000
#         state_t = np.empty((num_sample,4))
#         proto_t = np.empty((num_sample,protos.shape[1]))
        
#         encoded = []
#         proto = []
#         actual_proto = []
#         lst_proto = []
        
#         idx = np.random.choice(state.shape[0], size=num_sample, replace=False)
#         print('starting to load 50k')
#         for ix,x in enumerate(idx):
#             print(ix)
#             state_t[ix] = state[x]
#             fn = eps[x]
#             idx_ = index[x]
#             ep = np.load(fn)
#             #pixels.append(ep['observation'][idx_])

#             with torch.no_grad():
#                 obs = ep['observation'][idx_]
#                 obs = torch.as_tensor(obs.copy(), device=torch.device('cuda')).unsqueeze(0)
#                 z = agent.encoder(obs)
#                 encoded.append(z)
#                 z = agent.predictor(z)
#                 z = agent.projector(z)
#                 z = F.normalize(z, dim=1, p=2) 
#                 proto_t[ix]=z.cpu().numpy()

        
#         print('data loaded in',state.shape[0])

#         covar = np.cov(proto_t.T)
#         print(covar.shape)
#         U, S, Vh = scipy.linalg.svd(covar)
#         print(S)
#         plt.plot(S)
#         plt.clf()
#         fig, ax = plt.subplots()
#         ax.plot(S)
#         ax.set_title('singular values')
#         plt.savefig(f"./knn_output/singular_value_{model}_{model_step}.png")
           
        
        num_sample=500
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
                    
        df.loc[0,0] = count00
        df.loc[0,1] = count01
        df.loc[1,1] = count11
        df.loc[1,0] = count10
        labels=df
        plt.clf()
        fig, ax = plt.subplots()
        sns.heatmap(df, cmap="Blues_r",cbar=False, annot=labels).invert_yaxis()
        ax.set_title('data percentage')
        plt.savefig(f"./gc_knn_output/data_pct_model{model}_{model_step}.png")

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

        plt.clf()
        fig, ax = plt.subplots()
        ax.scatter(goal_array[:,0], goal_array[:,1])
        plt.savefig(f"./gc_knn_output/mesh.png")
        lst=[]
        goal_array = torch.as_tensor(goal_array, device=torch.device('cuda'))

        plt.clf()
        fig, ax = plt.subplots()
        ax.scatter(a[:,0], a[:,1])
        plt.savefig(f"./gc_knn_output/samples.png")
        a = torch.as_tensor(a,device=torch.device('cuda'))

        state_dist = torch.norm(goal_array[:,None,:]  - a[None,:,:], dim=2, p=2)
        all_dists_state, _state = torch.topk(state_dist, 10, dim=1, largest=False)


        ##encoded goals w/ no velocity 

        actual_proto_no_v=[]
        encoded_no_v=[]
        proto_no_v = []
        #no velocity goals 
        actual_proto_no_v = []
        goal_array = ndim_grid(2,10)
        for ix,x in enumerate(goal_array):
            if (-.2<x[0]<.2 and -.02<x[1]<.02) or (-.02<x[0]<.02 and -.2<x[1]<.2):
                lst.append(ix)
        goal_array=np.delete(goal_array, lst,0)

        lst_proto = []
        goal_lst = []
        for x in goal_array:
            
            goal_lst.append(np.tile(x[None,:], (num_sample,1)))

            with torch.no_grad():
                with eval_env_goal.physics.reset_context():
                    time_step_init = eval_env_goal.physics.set_state(np.array([x[0].item(), x[1].item(),0,0]))

                time_step_init = eval_env_goal._env.physics.render(height=84, width=84, camera_id=dict(quadruped=2).get('point_mass_maze', 0))
                time_step_init = np.transpose(time_step_init, (2,0,1))
                time_step_init = np.tile(time_step_init, (3,1,1))
                obs = torch.as_tensor(time_step_init, device=torch.device('cuda')).unsqueeze(0)
                
            
                z = encoder.encoder(obs)
                encoded_no_v.append(z.tile((num_sample,1)))
                #x = torch.as_tensor(obs, device=torch.device('cuda'))
                
        print('ndim_grid no velocity: therere {} unique prototypes that are neighbors to {} datapoints'.format(len(set(lst_proto)), goal_array.shape[0]))

        encoded_no_v = torch.cat(encoded_no_v,axis=0)
        goal_lst = np.concatenate(goal_lst)
        print(encoded_no_v.shape)


        actual_obs = []
        actual_goal = []
        actual_proto = []
        lst_proto = []
        act_encoded = []
        critic_encoded = []
        actions = []
        actual_goal_lst = []

        for x in idx:
            fn = eps[x]
            idx_ = index[x]
            ep = np.load(fn)
            #pixels.append(ep['observation'][idx_])

            with torch.no_grad():
                obs = ep['observation'][idx_]
                goal = ep['goal'][idx_]
                goal = np.tile(goal, (3,1,1))
                actual_goal_lst.append(ep['goal_state'][idx_][None,:])
                obs = torch.as_tensor(obs.copy(), device=torch.device('cuda')).unsqueeze(0)
                goal = torch.as_tensor(goal.copy(), device=torch.device('cuda')).unsqueeze(0)
                z = encoder.encoder(obs)
                g = encoder.encoder(goal)
                actual_obs.append(z)
                actual_goal.append(g)

        actual_obs = torch.cat(actual_obs,axis=0)
        actual_goal = torch.cat(actual_goal,axis=0)
        actual_goal_lst = np.concatenate(actual_goal_lst, axis=0)
        print('catual goal',actual_goal_lst.shape)
        
        obs_goal_actual = torch.cat([actual_obs, actual_goal], dim=-1)
        
        actual_obs_ = actual_obs.clone() 
        actual_obs = actual_obs.tile((goal_array.shape[0],1))
        obs_goal = torch.cat([actual_obs, encoded_no_v], dim=-1)
        a = np.tile(state, (goal_array.shape[0],1))
        
        obs_goal = torch.cat([actual_obs, encoded_no_v], dim=-1)

        if ac:
            z = actor.trunk(obs_goal)
            h = critic.trunk(obs_goal)

            z_actual = actor.trunk(obs_goal_actual)
            h_actual = critic.trunk(obs_goal_actual)
        else:
            z = agent.actor.trunk(obs_goal)
            h = agent.critic.trunk(obs_goal)

            z_actual = agent.actor.trunk(obs_goal_actual)
            h_actual = agent.critic.trunk(obs_goal_actual)
        
        #for ind in range(encoded.shape[0]):
        action = act(actual_obs, encoded_no_v, step=0, eval_mode=True)
        action_actual = act(actual_obs_, actual_goal, step=0, eval_mode=True)

        actor_dist = torch.norm(z_actual[:,None,:]  - z[None,:,:], dim=2, p=2)
        all_dists_actor, _actor = torch.topk(actor_dist, 10, dim=1, largest=False)
        
        critic_dist = torch.norm(h_actual[:,None,:]  - h[None,:,:], dim=2, p=2)
        all_dists_critic, _critic = torch.topk(critic_dist, 10, dim=1, largest=False)
        
        print(action.shape)
        #import IPython as ipy; ipy.embed(colors='neutral')
        action_dist = torch.norm(action_actual[:, None,:]  - action[None,:,:], dim=2, p=2)
        print(action_dist.shape)
        all_dists_action, _action = torch.topk(action_dist, 10, dim=1, largest=False)
        print(_action) 
        dist_matrices = [_actor, _critic, _action]
        names = [f"{model}_{model_step}_actor.gif", f"{model}_{model_step}_critic.gif", f"{model}_{model_step}_actions.gif"]
        final = np.concatenate([a, goal_lst], axis=1)
        #import IPython as ipy; ipy.embed(colors='neutral')
        for index_, dist_matrix in enumerate(dist_matrices):
            filenames=[]
            for ix in range(num_sample):
                print('encoded',ix)
                txt=''
                df = pd.DataFrame()

                for iz, z in enumerate(dist_matrix[ix,:]):
                    
                    if iz ==0:
                        
                        df.loc[dist_matrix[ix,:].shape[0]+10,'x'] = a[ix,0]
                        df.loc[dist_matrix[ix,:].shape[0]+10,'y'] = a[ix,1]
                        df.loc[dist_matrix[ix,:].shape[0]+10,'c'] = 'orange'
                        
                        df.loc[dist_matrix[ix,:].shape[0]+11,'x'] = actual_goal_lst[ix,0]
                        df.loc[dist_matrix[ix,:].shape[0]+11,'y'] = actual_goal_lst[ix,1]
                        df.loc[dist_matrix[ix,:].shape[0]+11,'c'] = 'red'
                        
                        df.loc[iz, 'x'] = final[z,0]
                        df.loc[iz, 'y'] = final[z,1]
                        df.loc[iz, 'c'] = 'blue'
                        
                        df.loc[dist_matrix[ix,:].shape[0], 'x'] = final[z,4]
                        df.loc[dist_matrix[ix,:].shape[0], 'y'] = final[z,5]
                        df.loc[dist_matrix[ix,:].shape[0], 'c'] = 'green'
                        
                        txt += ' ['+str(np.round(a[ix,0],2))+','+str(np.round(a[ix,1],2))+'] '
                        txt += ' goal: ['+str(np.round(actual_goal_lst[ix,0],4))+','+str(np.round(actual_goal_lst[ix,1],2))+'] '
                    else:                   
                        
                        df.loc[iz, 'x'] = final[z,0]
                        df.loc[iz, 'y'] = final[z,1]
                        df.loc[iz, 'c'] = 'blue'
                        
                        df.loc[dist_matrix[ix,:].shape[0]+iz, 'x'] = final[z,4]
                        df.loc[dist_matrix[ix,:].shape[0]+iz, 'y'] = final[z,5]
                        df.loc[dist_matrix[ix,:].shape[0]+iz, 'c'] = 'green'

                plt.clf()
                fig, ax = plt.subplots()
                palette = {
                                    'blue': 'tab:blue',
                                    'orange': 'tab:orange',
                                    'green': 'tab:green',
                                    'red' : 'tab:red'
                                }
                
            
                ax=sns.scatterplot(x="x", y="y",
                          hue="c", palette=palette,
                          data=df,legend=False)
                ax.set_title("\n".join(wrap(txt,75)))
                
                if index_==0:
                    file1= f"./gc_knn_output/actor_{ix}_model{model}_{model_step}.png"
                elif index_==1:
                    file1= f"./gc_knn_output/critic_{ix}_model{model}_{model_step}.png"
                elif index_==2:
                    file1= f"./gc_knn_output/actions_{ix}_model{model}_{model_step}.png"
                plt.savefig(file1)
                filenames.append(file1)

            if len(filenames)>100:
                filenames=filenames[:100]
            with imageio.get_writer(os.path.join('./gc_knn_output/',names[index_]), mode='I') as writer:
                for file in filenames:
                    image = imageio.imread(file)
                    writer.append_data(image)

            gif = imageio.mimread(os.path.join('./gc_knn_output/',names[index_]))

            imageio.mimsave(os.path.join('./gc_knn_output/',names[index_]), gif, fps=.5)

        
        
        
        
        
        
        