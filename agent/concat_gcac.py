import hydra
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from collections import OrderedDict

import utils
from dm_control.utils import rewards

#only concatenating goals to actor's layers

class Actor(nn.Module):
    def __init__(self, obs_dim, goal_dim, action_dim, hidden_dim):
        super().__init__()
        
        self.fc1 = nn.Linear(obs_dim+goal_dim, hidden_dim)
        self.ln = nn.LayerNorm(hidden_dim+goal_dim)
        self.tanh = nn.Tanh()
        self.fc2 = nn.Linear(hidden_dim+goal_dim, hidden_dim)
        self.relu = nn.ReLU(inplace=True)
        self.fc3 = nn.Linear(hidden_dim+goal_dim, action_dim)
       
        self.apply(utils.weight_init)

    def forward(self, obs, goal, std):
        
        obs_goal = torch.cat([obs, goal], dim=-1)
        x = self.ln1(self.fc1(obs_goal))
        x = self.tanh(x)
        print('goal shape', goal.shape)
        print('output of tanh layer shape', x.shape)
        x = torch.cat([x, goal], dim=-1)
        #check if hidden dim & goals are compatible 
        x = self.fc2(x)
        x = self.relu(x)
        x = torch.cat([x, goal], dim=-1)
        x = self.fc3(x)
        mu = torch.tanh(x)
        std = torch.ones_like(mu) * std
        dist = utils.TruncatedNormal(mu, std)
        
        return dist


class Critic(nn.Module):
    def __init__(self, obs_dim, goal_dim, action_dim, hidden_dim):
        super().__init__()

        self.q1_net = nn.Sequential(
            nn.Linear(obs_dim + goal_dim + action_dim, hidden_dim),
            nn.LayerNorm(hidden_dim + goal_dim), nn.Tanh(),
            nn.Linear(hidden_dim + goal_dim, hidden_dim), nn.ReLU(inplace=True),
            nn.Linear(hidden_dim, 1))

        self.q2_net = nn.Sequential(
            nn.Linear(obs_dim + goal_dim + action_dim, hidden_dim),
            nn.LayerNorm(hidden_dim + goal_dim), nn.Tanh(),
            nn.Linear(hidden_dim + goal_dim, hidden_dim), nn.ReLU(inplace=True),
            nn.Linear(hidden_dim + goal_dim, 1))

        self.apply(utils.weight_init)

    def forward(self, obs, goal, action):
        obs_action = torch.cat([obs, goal, action], dim=-1)
        q1 = self.q1_net(obs_action)
        q2 = self.q2_net(obs_action)

        return q1, q2


class CONCAT_GCACAgent:
    def __init__(self,
                 name,
                 obs_shape,
                 action_shape,
                 goal_shape,
                 device,
                 lr,
                 hidden_dim,
                 critic_target_tau,
                 stddev_schedule,
                 nstep,
                 batch_size,
                 stddev_clip,
                 use_tb,
                 has_next_action=False):
        self.action_dim = action_shape[0]
        self.hidden_dim = hidden_dim
        self.lr = lr
        self.device = device
        self.critic_target_tau = critic_target_tau
        self.use_tb = use_tb
        self.stddev_schedule = stddev_schedule
        self.stddev_clip = stddev_clip

        # models
        self.actor = Actor(obs_shape[0], goal_shape[0], action_shape[0],
                           hidden_dim).to(device)

        self.critic = Critic(obs_shape[0], goal_shape[0], action_shape[0],
                             hidden_dim).to(device)
        self.critic_target = Critic(obs_shape[0], goal_shape[0], action_shape[0],
                                    hidden_dim).to(device)
        self.critic_target.load_state_dict(self.critic.state_dict())

        # optimizers
        self.actor_opt = torch.optim.Adam(self.actor.parameters(), lr=lr)
        self.critic_opt = torch.optim.Adam(self.critic.parameters(), lr=lr)

        self.train()
        self.critic_target.train()

    def train(self, training=True):
        self.training = training
        self.actor.train(training)
        self.critic.train(training)

    def act(self, obs, goal, step, eval_mode):
        obs = torch.as_tensor(obs, device=self.device).unsqueeze(0)
        goal = torch.as_tensor(goal, device=self.device).unsqueeze(0).float()
        stddev = utils.schedule(self.stddev_schedule, step)
        policy = self.actor(obs, goal, stddev)
        if eval_mode:
            action = policy.mean
        else:
            action = policy.sample(clip=None)
            if step < self.num_expl_steps:
                action.uniform_(-1.0, 1.0)
        return action.cpu().numpy()[0]

    def update_critic(self, obs, goal, action, reward, discount, next_obs, step):
        metrics = dict()

        with torch.no_grad():
            stddev = utils.schedule(self.stddev_schedule, step)
            dist = self.actor(next_obs, goal, stddev)
            next_action = dist.sample(clip=self.stddev_clip)
            target_Q1, target_Q2 = self.critic_target(next_obs, goal, next_action)
            target_V = torch.min(target_Q1, target_Q2)
            target_Q = reward + (discount * target_V)

        Q1, Q2 = self.critic(obs, goal, action)
        critic_loss = F.mse_loss(Q1, target_Q) + F.mse_loss(Q2, target_Q)

        if self.use_tb:
            metrics['critic_target_q'] = target_Q.mean().item()
            metrics['critic_q1'] = Q1.mean().item()
            metrics['critic_q2'] = Q2.mean().item()
            metrics['critic_loss'] = critic_loss.item()

        # optimize critic
        self.critic_opt.zero_grad(set_to_none=True)
        critic_loss.backward()
        self.critic_opt.step()
        return metrics

    def update_actor(self, obs, goal, action, step):
        metrics = dict()

        stddev = utils.schedule(self.stddev_schedule, step)
        policy = self.actor(obs, goal, stddev)

        Q1, Q2 = self.critic(obs, goal, policy.sample(clip=self.stddev_clip))
        Q = torch.min(Q1, Q2)

        actor_loss = -Q.mean()

        # optimize actor
        self.actor_opt.zero_grad(set_to_none=True)
        actor_loss.backward()
        self.actor_opt.step()

        if self.use_tb:
            metrics['actor_loss'] = actor_loss.item()
            metrics['actor_ent'] = policy.entropy().sum(dim=-1).mean().item()

        return metrics

    def update(self, replay_iter, step):
        metrics = dict()

        batch = next(replay_iter)
        obs, action, reward, discount, next_obs, goal = utils.to_torch(
            batch, self.device)
        obs = obs.reshape(-1, 4).float()
        next_obs = next_obs.reshape(-1, 4).float()
        goal = goal.reshape(-1, 2).float()
        action = action.reshape(-1, 2).float()
        reward = reward.reshape(-1, 1).float()
        discount = discount.reshape(-1, 1).float()
        reward = reward.float()

        if self.use_tb:
            metrics['batch_reward'] = reward.mean().item()

        # update critic
        metrics.update(
            self.update_critic(obs, goal, action, reward, discount, next_obs, step))

        # update actor
        metrics.update(self.update_actor(obs, goal, action, step))

        # update critic target
        utils.soft_update_params(self.critic, self.critic_target,
                                 self.critic_target_tau)

        return metrics
