import warnings

warnings.filterwarnings('ignore', category=DeprecationWarning)

import os
#HOME="/home/mag1038"

os.environ['MKL_SERVICE_FORCE_INTEL'] = '1'
os.environ['MUJOCO_GL'] = 'egl'
#os.environ["LD_LIBRARY_PATH"] = f"{HOME}/.mujoco/mujoco210/bin:{os.environ['LD_LIBRARY_PATH']}"

from pathlib import Path

import hydra
import numpy as np
import torch
import wandb
from dm_env import specs
import pickle
import json

import dmc
import utils
from logger import Logger
from replay_buffer import ReplayBufferStorage, make_replay_loader
from video import TrainVideoRecorder, VideoRecorder
import pickle

torch.backends.cudnn.benchmark = True

from dmc_benchmark import PRIMAL_TASKS


def get_encoding(agent, time_step, num_aug=5):
    obs = torch.tensor(time_step.observation).cuda()[None]
    auglist = []
    with torch.no_grad():
        for _ in range(num_aug):
            auglist.append(agent.aug_and_encode(obs))
    return torch.mean(torch.stack(auglist), 0).cpu()

def make_agent(obs_type, obs_spec, action_spec, num_expl_steps, cfg):
    cfg.obs_type = obs_type
    cfg.obs_shape = obs_spec.shape
    cfg.action_shape = action_spec.shape
    cfg.num_expl_steps = num_expl_steps
    return hydra.utils.instantiate(cfg)


class Workspace:
    def __init__(self, cfg):
        self.work_dir = Path.cwd()
        print(f"workspace: {self.work_dir}")
        self.log2 = []

        self.cfg = cfg
        utils.set_seed_everywhere(cfg.seed)
        self.device = torch.device(cfg.device)

        # create logger
        if cfg.use_wandb:
            exp_name = '_'.join([
                cfg.experiment, cfg.agent.name, cfg.domain, cfg.obs_type,
                str(cfg.seed)
            ])
            wandb.init(project="urlb", group=cfg.agent.name, name=exp_name)

        self.logger = Logger(self.work_dir,
                             use_tb=cfg.use_tb,
                             use_wandb=cfg.use_wandb)
        # create envs
        try:
            task = PRIMAL_TASKS[self.cfg.domain]
        except:
            task = self.cfg.domain
        self.train_env = dmc.make(task, cfg.obs_type, cfg.frame_stack,
                                  cfg.action_repeat, cfg.seed, time_limit=20)
        self.eval_env = dmc.make(task, cfg.obs_type, cfg.frame_stack,
                                 cfg.action_repeat, cfg.seed, time_limit=20)

        # create agent
        self.agent = make_agent(cfg.obs_type,
                                self.train_env.observation_spec(),
                                self.train_env.action_spec(),
                                cfg.num_seed_frames // cfg.action_repeat,
                                cfg.agent)

        self.pretrained_agent = make_agent(cfg.obs_type,
                                self.train_env.observation_spec(),
                                self.train_env.action_spec(),
                                cfg.num_seed_frames // cfg.action_repeat,
                                cfg.agent)

        pretrained_agent = self.load_snapshot_fixed()["agent"]
        self.pretrained_agent.init_from(pretrained_agent)
        self.pretrained_agent.encoder.cuda()


        # get meta specs
        meta_specs = self.agent.get_meta_specs()
        # create replay buffer
        data_specs = (self.train_env.observation_spec(),
                      self.train_env.action_spec(),
                      specs.Array((1,), np.float32, 'reward'),
                      specs.Array((1,), np.float32, 'discount'))

        # create data storage
        self.replay_storage = ReplayBufferStorage(data_specs, meta_specs,
                                                  self.work_dir / 'buffer')

        # create replay buffer
        self.replay_loader = make_replay_loader(self.replay_storage,
                                                cfg.replay_buffer_size,
                                                cfg.batch_size,
                                                cfg.replay_buffer_num_workers,
                                                True, cfg.nstep, cfg.discount)
        self._replay_iter = None

        # create video recorders
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
        self.play_dataset = []
        self.buffer_size = 2000

        self.encodings = torch.zeros(self.buffer_size, 500+1, self.agent.encoder.repr_dim)
        self.actions = torch.zeros(self.buffer_size, 499+1, 2)
        self.physics = torch.zeros(self.buffer_size, 500+1, 4)
        self.cid = 0

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
    def replay_iter(self):
        if self._replay_iter is None:
            self._replay_iter = iter(self.replay_loader)
        return self._replay_iter

    def insert_to_buffer(self, encodings, actions, physics):
        if isinstance(encodings, list):
            encodings = torch.stack(encodings)
            actions = torch.stack(actions)
            physics = torch.stack(physics)
        self.encodings[(self.cid%self.buffer_size)] = encodings
        self.actions[(self.cid%self.buffer_size)] = actions
        self.physics[(self.cid%self.buffer_size)] = physics
        self.cid = (self.cid + 1)
        if (self.cid % 100) == 0:
            with open("/home/maxgold/workspace/explore/proto_explore/url_benchmark/pretrain_dataset.pkl", "wb") as f:
                pickle.dump((self.encodings, self.actions, self.physics), f)

    def eval(self, states):
        step, episode, total_reward = 0, 0, 0
        eval_until_episode = utils.Until(self.cfg.num_eval_episodes)
        meta = self.agent.init_meta()
        while eval_until_episode(episode):
            dataset = {"obs": [], "action": [], "physics": []}
            time_step = self.eval_env.reset()
            dataset["obs"].append(time_step.observation)
            dataset["physics"].append(time_step.physics)
            self.video_recorder.init(self.eval_env, enabled=(episode == 0))
            while not time_step.last():
                with torch.no_grad(), utils.eval_mode(self.agent):
                    action = self.agent.act(time_step.observation,
                                            meta,
                                            self.global_step,
                                            eval_mode=True)
                dataset["action"].append(action)
                time_step = self.eval_env.step(action)
                dataset["obs"].append(time_step.observation)
                dataset["physics"].append(time_step.physics)
                self.video_recorder.record(self.eval_env)
                total_reward += time_step.reward
                step += 1

            episode += 1
            self.video_recorder.save(f'{self.global_frame}.mp4')
            self.play_dataset.append(dataset)
#        if self.global_step % int(1e5) == 0:
#            if len(states):
#                proto2d = visualize_prototypes(self.agent, states)
#                plt.clf()
#                fig, ax = plt.subplots()
#                ax.scatter(proto2d[:,0], proto2d[:,1])
#                plt.savefig(f"./{self.global_step}_proto2d.png")

        with self.logger.log_and_dump_ctx(self.global_frame, ty='eval') as log:
            log('episode_reward', total_reward / episode)
            log('episode_length', step * self.cfg.action_repeat / episode)
            log('episode', self.global_episode)
            log('step', self.global_step)
        with open("/home/maxgold/workspace/explore/proto_explore/url_benchmark/play_dataset.pkl", "wb") as f:
            pickle.dump(self.play_dataset, f)

    def train(self):
        # predicates
        self.xylist = []
        train_until_step = utils.Until(self.cfg.num_train_frames,
                                       self.cfg.action_repeat)
        seed_until_step = utils.Until(self.cfg.num_seed_frames,
                                      self.cfg.action_repeat)
        eval_every_step = utils.Every(self.cfg.eval_every_frames,
                                      self.cfg.action_repeat)
        encodings = []
        actions = []
        physics = []

        episode_step, episode_reward = 0, 0
        time_step = self.train_env.reset()
        encodings.append(get_encoding(self.pretrained_agent, time_step).squeeze())
        physics.append(torch.tensor(time_step.physics))
        self.xylist.append(time_step.physics)
        meta = self.agent.init_meta()
        self.replay_storage.add(time_step, meta)
        self.train_video_recorder.init(time_step.observation)
        metrics = None
        while train_until_step(self.global_step):
            if time_step.last():
                self._global_episode += 1
                self.train_video_recorder.save(f'{self.global_frame}.mp4')
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
                self.insert_to_buffer(encodings, actions, physics)
                encodings = []
                actions = []
                physics = []
                time_step = self.train_env.reset()
                self.xylist.append(time_step.physics)
                meta = self.agent.init_meta()
                self.replay_storage.add(time_step, meta)
                self.train_video_recorder.init(time_step.observation)
                # try to save snapshot
                if self.global_frame in self.cfg.snapshots:
                    self.save_snapshot()
                episode_step = 0
                episode_reward = 0
                encodings.append(get_encoding(self.pretrained_agent, time_step).squeeze())
                physics.append(torch.tensor(time_step.physics))

            # try to evaluate
            if eval_every_step(self.global_step):
                self.logger.log('eval_total_time', self.timer.total_time(),
                                self.global_frame)
                self.eval()

            meta = self.agent.update_meta(meta, self.global_step, time_step)
            # sample action
            with torch.no_grad(), utils.eval_mode(self.agent):
                action = self.agent.act(time_step.observation,
                                        meta,
                                        self.global_step,
                                        eval_mode=False)
                actions.append(torch.tensor(action))

            # try to update the agent
            if not seed_until_step(self.global_step):
                metrics = self.agent.update(self.replay_iter, self.global_step)
                if metrics != {}:
                    self.log2.append(metrics)
                with (self.work_dir / "metric_log.json").open("wb") as f:
                    pickle.dump(self.log2, f)
                self.logger.log_metrics(metrics, self.global_frame, ty='train')

            if self.global_step % 50000 == 0:
                with (self.work_dir / "heatmap.pkl").open("wb") as f:
                    pickle.dump(self.xylist, f)

            # take env step
            time_step = self.train_env.step(action)
            states.append(time_step.observation)
            encodings.append(get_encoding(self.pretrained_agent, time_step).squeeze())
            physics.append(torch.tensor(time_step.physics))
            self.xylist.append(time_step.physics)
            episode_reward += time_step.reward
            self.replay_storage.add(time_step, meta)
            self.train_video_recorder.record(time_step.observation)
            episode_step += 1
            self._global_step += 1

    def save_snapshot(self):
        if "point" in self.cfg.domain:
            if "no_goal" in self.cfg.domain:
                tmp = self.cfg.domain
            else:
                tmp = "_".join(self.cfg.domain.split("_")[:3])
            self.cfg.snapshot_dir = f"../../../pretrained_models/{self.cfg.obs_type}/{tmp}/{self.cfg.agent.name}/{self.cfg.seed}"
        snapshot_dir = self.work_dir / Path(self.cfg.snapshot_dir)
        snapshot_dir.mkdir(exist_ok=True, parents=True)
        snapshot = snapshot_dir / f'snapshot_{self.global_frame}.pt'
        keys_to_save = ['agent', '_global_step', '_global_episode']
        payload = {k: self.__dict__[k] for k in keys_to_save}
        with snapshot.open('wb') as f:
            torch.save(payload, f)

    def load_snapshot_fixed(self):
        snapshot_dir = Path(f"models/pixels/{self.cfg.domain}/proto_proto")
        snapshot_ts = 2000000

        def try_load(seed):
            snapshot = (
                Path("/home/maxgold/workspace/explore/proto_explore/url_benchmark")
                / snapshot_dir
                / str(seed)
                / f"snapshot_{snapshot_ts}.pt"
            )
            # import IPython as ipy; ipy.embed(colors='neutral')
            if not snapshot.exists():
                return None
            with snapshot.open("rb") as f:
                payload = torch.load(f)
            return payload

        # try to load current seed
        payload = try_load(2)
        if payload is not None:
            return payload
        # otherwise try random seed
        while True:
            seed = np.random.randint(1, 11)
            payload = try_load(seed)
            if payload is not None:
                return payload
        return None


@hydra.main(config_path='.', config_name='pretrain')
def main(cfg):
    from pretrain import Workspace as W
    print(cfg)
    root_dir = Path.cwd()
    workspace = W(cfg)
    snapshot = root_dir / 'snapshot.pt'
    res = {}
    res["type"] = "pretrain"
    import copy
    tcfg = copy.copy(cfg)
    for k, v in tcfg.items():
        if type(v) == dict:
            res[k] = {}
            for k2, v2 in v.items():
                res[k][k2] = v2
        else:
            res[k] = v
    import pickle
    with (root_dir / "cfg.json").open("wb") as f:
        pickle.dump(res, f)


    if snapshot.exists():
        print(f'resuming: {snapshot}')
        workspace.load_snapshot()
    workspace.train()


if __name__ == '__main__':
    main()
