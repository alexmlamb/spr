import argparse
import copy
from datetime import datetime
import os
import subprocess

import atari_py
import torch
import torch.nn.functional as F
import numpy as np
from sklearn.metrics import f1_score as compute_f1_score
from collections import defaultdict
import wandb
import matplotlib.pyplot as plt
import io
from PIL import Image

train_encoder_methods = ["infonce-stdim"]


def get_argparser():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pretraining-steps', type=int, default=100000,
                        help='Number of steps to pretrain representations (default: 100000)')
    parser.add_argument('--method', type=str, default='infonce-stdim',
                        choices=train_encoder_methods,
                        help='Method to use for training representations (default: infonce-stdim)')
    parser.add_argument('--encoder-lr', type=float, default=3e-4,
                        help='Learning Rate foe learning representations (default: 5e-4)')
    parser.add_argument('--epochs', type=int, default=20,
                        help='Number of epochs for  (default: 20)')
    parser.add_argument('--pretrain-epochs', type=int, default=20,
                        help='Number of epochs to pretrain model and encoder for (default: 20 (same as --epochs)).')
    parser.add_argument('--cuda-id', type=int, default=0,
                        help='CUDA device index')
    parser.add_argument('--seed', type=int, default=42,
                        help='Random seed to use')
    parser.add_argument('--encoder-type', type=str, default="Nature", choices=["Impala", "Nature"],
                        help='Encoder type (Impala or Nature)')
    parser.add_argument('--feature-size', type=int, default=256,
                        help='Size of features')

    # Integrated Model Args
    parser.add_argument('--integrated-model', action="store_true",
                        default=False, help='Use an encoder with the model built in.')
    parser.add_argument('--multi-step-training', action="store_true",
                        default=False, help='Train an integrated model over multiple jumps')
    parser.add_argument('--max-jump-length', type=int, default=5,
                        help='Maximum number of steps to use in multi-step training.')
    parser.add_argument('--visualization_jumps', type=int, default=30,
                        help='Maximum number of steps to use in multi-step training.')
    parser.add_argument('--min-jump-length', type=int, default=1,
                        help='Minimum number of steps to use in multi-step training.')
    parser.add_argument('--framestack-infomax', action="store_true",
                        default=False, help='Use a framestack in the infomax (integrated model only).')
    parser.add_argument('--global-loss', action="store_true", default=False,
                        help='Use a global L2 loss in addition to the standard local-global loss.')
    parser.add_argument('--film', action="store_true", default=False,
                        help='Use FILM instead of an outer product to integrate actions')
    parser.add_argument('--layernorm', action="store_true", default=False,
                        help='Use layernorm with FILM.')
    parser.add_argument('--bilinear-global-loss', action="store_true", default=False,
                        help='Use a global bilinear loss in addition to the standard local-global loss.')
    parser.add_argument('--detach-target', action="store_true", default=False,
                        help='Detach the target representation.')
    parser.add_argument('--noncontrastive-global-loss', action="store_true", default=False,
                        help='Use a global L2 loss in addition to the standard local-global loss.')
    parser.add_argument('--dense-supervision', action="store_true", default=False,
                        help='Evaluate L2 and cosine similarity at each jump.')
    parser.add_argument("--noncontrastive-loss-weight", default=10.0, type=float,
                        help="Weight for noncontrastive global loss in shared loss.")
    parser.add_argument("--hard-neg-factor", type=int, default=0,
                        help="How many hard negative action samples to use.")
    parser.add_argument("--dropout-prob", type=float, default=0.0,
                        help="How much dropout to use in the model and reward predictor.")
    parser.add_argument('--online-agent-training', action="store_true",
                        default=False, help='Train agent on real data alongside integrated model.')
    parser.add_argument('--probe-lr', type=float, default=3e-4)

    parser.add_argument("--patience", type=int, default=100)
    parser.add_argument("--end-with-relu", action='store_true', default=False)
    parser.add_argument("--wandb-proj", type=str, default="awm")
    parser.add_argument("--name", type=str, default="", help="Name for run in wandb.")
    parser.add_argument("--num_rew_evals", type=int, default=10)
    parser.add_argument("--collect-mode", type=str, choices=["random_agent", "atari_zoo", "pretrained_ppo"],
                        default="random_agent")

    # MBPO Args
    parser.add_argument("--total_steps", type=int, default=100000)
    parser.add_argument('--fake-buffer-capacity', type=int, default=int(4e6),
                        help='Size of the replay buffer for rollout transitions')
    parser.add_argument("--rollout_length", type=int, default=1)
    parser.add_argument("--num_model_rollouts", type=int, default=400)
    parser.add_argument("--env_steps_per_epoch", type=int, default=1000)
    parser.add_argument("--updates_per_step", type=int, default=20)
    parser.add_argument("--initial_exp_steps", type=int, default=5000)
    parser.add_argument('--forward-hidden-size', type=int, default=512, help='Hidden Size for the Forward Model MLP')
    parser.add_argument('--sd_loss_coeff', type=int, default=10, help='Coefficient for the dynamics loss')
    parser.add_argument("--reward-loss-weight", default=1.0, type=float,
                        help="Weight for reward in shared loss.")
    # Rainbow Args
    parser.add_argument('--id', type=str, default='default', help='Experiment ID')
    parser.add_argument('--disable-cuda', action='store_true', help='Disable CUDA')
    parser.add_argument('--game', type=str, default='space_invaders', choices=atari_py.list_games(), help='ATARI game')
    parser.add_argument('--T-max', type=int, default=int(50e6), metavar='STEPS',
                        help='Number of training steps (4x number of frames)')
    parser.add_argument('--max-episode-length', type=int, default=int(108e3), metavar='LENGTH',
                        help='Max episode length in game frames (0 to disable)')
    parser.add_argument('--history-length', type=int, default=4, metavar='T',
                        help='Number of consecutive states processed')
    parser.add_argument('--architecture', type=str, default='canonical', choices=['canonical', 'data-efficient'],
                        metavar='ARCH', help='Network architecture')
    parser.add_argument('--hidden-size', type=int, default=256, metavar='SIZE', help='Network hidden size')
    parser.add_argument('--noisy-std', type=float, default=0.1, metavar='σ',
                        help='Initial standard deviation of noisy linear layers')
    parser.add_argument('--atoms', type=int, default=51, metavar='C', help='Discretised size of value distribution')
    parser.add_argument('--V-min', type=float, default=-10, metavar='V', help='Minimum of value distribution support')
    parser.add_argument('--V-max', type=float, default=10, metavar='V', help='Maximum of value distribution support')
    parser.add_argument('--model', type=str, metavar='PARAMS', help='Pretrained model (state dict)')
    parser.add_argument('--memory-capacity', type=int, default=int(1e6), metavar='CAPACITY',
                        help='Experience replay memory capacity')
    parser.add_argument('--replay-frequency', type=int, default=4, metavar='k',
                        help='Frequency of sampling from memory')
    parser.add_argument('--priority-exponent', type=float, default=0., metavar='ω',
                        help='Prioritised experience replay exponent (originally denoted α)')
    parser.add_argument('--priority-weight', type=float, default=1.0, metavar='β',
                        help='Initial prioritised experience replay importance sampling weight')
    parser.add_argument('--multi-step', type=int, default=1, metavar='n', help='Number of steps for multi-step return')
    parser.add_argument('--discount', type=float, default=0.99, metavar='γ', help='Discount factor')
    parser.add_argument('--target-update', type=int, default=int(1e3), metavar='τ',
                        help='Number of steps after which to update target network')
    parser.add_argument('--reward-clip', type=int, default=1, metavar='VALUE', help='Reward clipping (0 to disable)')
    parser.add_argument('--learning-rate', type=float, default=0.0001, metavar='η', help='Learning rate')
    parser.add_argument('--adam-eps', type=float, default=1.5e-4, metavar='ε', help='Adam epsilon')
    parser.add_argument('--batch-size', type=int, default=32, metavar='SIZE', help='Batch size')
    parser.add_argument('--learn-start', type=int, default=int(20e3), metavar='STEPS',
                        help='Number of steps before starting training')
    parser.add_argument('--evaluate', action='store_true', help='Evaluate only')
    parser.add_argument('--evaluation-interval', type=int, default=5000, metavar='STEPS',
                        help='Number of training steps between evaluations')
    parser.add_argument('--evaluation-episodes', type=int, default=5, metavar='N',
                        help='Number of evaluation episodes to average over')
    parser.add_argument('--evaluation-size', type=int, default=500, metavar='N',
                        help='Number of transitions to use for validating Q')
    parser.add_argument('--render', action='store_true', help='Display screen (testing only)')
    parser.add_argument('--enable-cudnn', action='store_true', help='Enable cuDNN (faster but nondeterministic)')

    return parser


def set_seeds(seed):
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    np.random.seed(seed)

    if torch.cuda.is_available():
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True


def calculate_accuracy(preds, y):
    preds = preds >= 0.5
    labels = y >= 0.5
    acc = preds.eq(labels).sum().float() / labels.numel()
    return acc


def calculate_multiclass_f1_score(preds, labels):
    preds = torch.argmax(preds, dim=1).detach().numpy()
    labels = labels.numpy()
    f1score = compute_f1_score(labels, preds, average="weighted")
    return f1score


def calculate_multiclass_accuracy(preds, labels):
    preds = torch.argmax(preds, dim=1)
    acc = float(torch.sum(torch.eq(labels, preds)).data) / labels.size(0)
    return acc


# Thanks Bjarten! (https://github.com/Bjarten/early-stopping-pytorch)
class EarlyStopping(object):
    """Early stops the training if validation loss doesn't improve after a given patience."""

    def __init__(self, patience=7, verbose=False, wandb=None, name=""):
        """
        Args:
            patience (int): How long to wait after last time validation loss improved.
                            Default: 7
            verbose (bool): If True, prints a message for each validation loss improvement.
                            Default: False
        """
        self.patience = patience
        self.verbose = verbose
        self.counter = 0
        self.best_score = None
        self.early_stop = False
        self.val_acc_max = 0.
        self.name = name
        self.wandb = wandb

    def __call__(self, val_acc, model):

        score = val_acc

        if self.best_score is None:
            self.best_score = score
            self.save_checkpoint(val_acc, model)
        elif score <= self.best_score:
            self.counter += 1
            print(f'EarlyStopping for {self.name} counter: {self.counter} out of {self.patience}')
            if self.counter >= self.patience:
                self.early_stop = True
                print(f'{self.name} has stopped')

        else:
            self.best_score = score
            self.save_checkpoint(val_acc, model)
            self.counter = 0

    def save_checkpoint(self, val_acc, model):
        '''Saves model when validation loss decrease.'''
        if self.verbose:
            print(
                f'Validation accuracy increased for {self.name}  ({self.val_acc_max:.6f} --> {val_acc:.6f}).  Saving model ...')

        save_dir = self.wandb.run.dir
        torch.save(model.state_dict(), save_dir + "/" + self.name + ".pt")
        self.val_acc_max = val_acc


class Cutout(object):
    """Randomly mask out one or more patches from an image.
    Args:
        n_holes (int): Number of patches to cut out of each image.
        length (int): The length (in pixels) of each square patch.
    """

    def __init__(self, n_holes, length):
        self.n_holes = n_holes
        self.length = length

    def __call__(self, img):
        """
        Args:
            img (Tensor): Tensor image of size (C, H, W).
        Returns:
            Tensor: Image with n_holes of dimension length x length cut out of it.
        """
        h = img.size(1)
        w = img.size(2)

        mask = np.ones((h, w), np.float32)

        for n in range(self.n_holes):
            y = np.random.randint(h)
            x = np.random.randint(w)

            y1 = np.clip(y - self.length // 2, 0, h)
            y2 = np.clip(y + self.length // 2, 0, h)
            x1 = np.clip(x - self.length // 2, 0, w)
            x2 = np.clip(x + self.length // 2, 0, w)

            mask[y1: y2, x1: x2] = 0.

        mask = torch.from_numpy(mask)
        mask = mask.expand_as(img)
        img = img * mask

        return img


# Simple ISO 8601 timestamped logger
def log(steps, avg_reward):
    s = 'T = ' + str(steps) + ' | Avg. reward: ' + str(avg_reward)
    print('[' + str(datetime.now().strftime('%Y-%m-%dT%H:%M:%S')) + '] ' + s)
    wandb.log({'avg_reward': avg_reward, 'total_steps': steps})


def set_learning_rate(optimizer, lr):
    """Sets the learning rate to the initial LR decayed by 10 every 30 epochs"""
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr


def fig2data(fig):
    """
    Borrowed from http://www.icare.univ-lille1.fr/tutorials/convert_a_matplotlib_figure
    @brief Convert a Matplotlib figure to a 4D numpy array with RGBA channels and return it
    @param fig a matplotlib figure
    @return a numpy 3D array of RGBA values
    """
    # draw the renderer
    fig.canvas.draw()

    # Get the RGBA buffer from the figure
    w, h = fig.canvas.get_width_height()
    buf = np.fromstring(fig.canvas.tostring_argb(), dtype=np.uint8)
    buf.shape = (w, h, 4)

    # canvas.tostring_argb give pixmap in ARGB mode. Roll the ALPHA channel to have it in RGBA mode
    buf = np.roll(buf, 3, axis=2)
    return buf

def save_to_pil():
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    im = Image.open(buf)
    im.load()
    return im


class appendabledict(defaultdict):
    def __init__(self, type_=list, *args, **kwargs):
        self.type_ = type_
        super().__init__(type_, *args, **kwargs)

    #     def map_(self, func):
    #         for k, v in self.items():
    #             self.__setitem__(k, func(v))

    def subslice(self, slice_):
        """indexes every value in the dict according to a specified slice

        Parameters
        ----------
        slice : int or slice type
            An indexing slice , e.g., ``slice(2, 20, 2)`` or ``2``.


        Returns
        -------
        sliced_dict : dict (not appendabledict type!)
            A dictionary with each value from this object's dictionary, but the value is sliced according to slice_
            e.g. if this dictionary has {a:[1,2,3,4], b:[5,6,7,8]}, then self.subslice(2) returns {a:3,b:7}
                 self.subslice(slice(1,3)) returns {a:[2,3], b:[6,7]}

         """
        sliced_dict = {}
        for k, v in self.items():
            sliced_dict[k] = v[slice_]
        return sliced_dict

    def append_update(self, other_dict):
        """appends current dict's values with values from other_dict

        Parameters
        ----------
        other_dict : dict
            A dictionary that you want to append to this dictionary


        Returns
        -------
        Nothing. The side effect is this dict's values change

         """
        for k, v in other_dict.items():
            self.__getitem__(k).append(v)
