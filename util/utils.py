"""
This util settings are attributed to my mentor's code: 
https://github.com/BIGBALLON/distribuuuu/blob/master/distribuuuu/utils.py
"""
import os
import shutil
import subprocess

import numpy as np
import torch
import torch.distributed as dist
import torchvision
import torchvision.transforms as transforms
from torch.optim.lr_scheduler import MultiStepLR, CosineAnnealingLR

from .config import cfg


def setup_distributed(backend="nccl", port=None):
    """
    Initialize distributed training environment.
    support both slurm and torch.distributed.launch
    """
    num_gpus = torch.cuda.device_count()

    if "SLURM_JOB_ID" in os.environ:
        rank = int(os.environ["SLURM_PROCID"])
        world_size = int(os.environ["SLURM_NTASKS"])
        node_list = os.environ["SLURM_NODELIST"]
        addr = subprocess.getoutput(f"scontrol show hostname {node_list} | head -n1")
        # specify master port
        if port is not None:
            os.environ["MASTER_PORT"] = str(port)
        elif "MASTER_PORT" not in os.environ:
            os.environ["MASTER_PORT"] = "29500"
        if "MASTER_ADDR" not in os.environ:
            os.environ["MASTER_ADDR"] = addr
        os.environ["WORLD_SIZE"] = str(world_size)
        os.environ["LOCAL_RANK"] = str(rank % num_gpus)
        os.environ["RANK"] = str(rank)
    else:
        rank = int(os.environ["RANK"])
        world_size = int(os.environ["WORLD_SIZE"])

    torch.cuda.set_device(rank % num_gpus)

    dist.init_process_group(
        backend=backend,
        world_size=world_size,
        rank=rank,
    )

    torch.backends.cudnn.benchmark = cfg.CUDNN.BENCHMARK


def scaled_all_reduce(tensors):
    """Performs the scaled all_reduce operation on the provided tensors.
    The input tensors are modified in-place. Currently supports only the sum
    reduction operator. The reduced values are scaled by the inverse size of the
    process group.
    """
    # There is no need for reduction in the single-proc case
    gpus = torch.distributed.get_world_size()
    if gpus == 1:
        return tensors
    # Queue the reductions
    reductions = []
    for tensor in tensors:
        reduction = torch.distributed.all_reduce(tensor, async_op=True)
        reductions.append(reduction)
    # Wait for reductions to finish
    for reduction in reductions:
        reduction.wait()
    # Scale the results
    for tensor in tensors:
        tensor.mul_(1.0 / gpus)
    return tensors


def construct_loader():
    """Constructs the data loader for the given dataset."""
    traindir = os.path.join(cfg.TRAIN.DATASET, cfg.TRAIN.SPLIT)
    valdir = os.path.join(cfg.TRAIN.DATASET, cfg.TEST.SPLIT)
    normalize = transforms.Normalize(
        mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
    )
    trainset = torchvision.datasets.ImageFolder(
        root=traindir,
        transform=transforms.Compose(
            [
                transforms.RandomResizedCrop(cfg.TRAIN.IM_SIZE),
                transforms.RandomHorizontalFlip(),
                transforms.ToTensor(),
                normalize,
            ]
        ),
    )
    # DistributedSampler
    train_sampler = torch.utils.data.distributed.DistributedSampler(
        trainset, shuffle=True
    )
    train_loader = torch.utils.data.DataLoader(
        trainset,
        batch_size=cfg.TRAIN.BATCH_SIZE,
        num_workers=cfg.TRAIN.WORKERS,
        pin_memory=cfg.TRAIN.PIN_MEMORY,
        sampler=train_sampler,
    )

    val_loader = torch.utils.data.DataLoader(
        torchvision.datasets.ImageFolder(
            root=valdir,
            transform=transforms.Compose(
                [
                    transforms.Resize(cfg.TEST.IM_SIZE),
                    transforms.CenterCrop(224),
                    transforms.ToTensor(),
                    normalize,
                ]
            ),
        ),
        batch_size=cfg.TEST.BATCH_SIZE,
        shuffle=False,
        num_workers=cfg.TRAIN.WORKERS,
        pin_memory=cfg.TRAIN.PIN_MEMORY,
    )
    return train_loader, val_loader


def construct_optimizer(model):
    return torch.optim.SGD(
        model.parameters(),
        lr=cfg.OPTIM.BASE_LR,
        momentum=cfg.OPTIM.MOMENTUM,
        weight_decay=cfg.OPTIM.WEIGHT_DECAY,
        dampening=cfg.OPTIM.DAMPENING,
        nesterov=cfg.OPTIM.NESTEROV,
    )


class AverageMeter(object):
    """Computes and stores the average and current value"""

    def __init__(self, name, fmt=":f"):
        self.name = name
        self.fmt = fmt
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count

    def __str__(self):
        fmtstr = "{name} {val" + self.fmt + "} ({avg" + self.fmt + "})"
        return fmtstr.format(**self.__dict__)


class ProgressMeter(object):
    def __init__(self, num_batches, meters, prefix=""):
        self.batch_fmtstr = self._get_batch_fmtstr(num_batches)
        self.meters = meters
        self.prefix = prefix

    def display(self, batch):
        entries = [self.prefix + self.batch_fmtstr.format(batch)]
        entries += [str(meter) for meter in self.meters]
        print(" | ".join(entries))

    def _get_batch_fmtstr(self, num_batches):
        num_digits = len(str(num_batches // 1))
        fmt = "{:" + str(num_digits) + "d}"
        return "[" + fmt + "/" + fmt.format(num_batches) + "]"


def accuracy(output, target, topk=(1,)):
    """Computes the accuracy over the k top predictions for the specified values of k"""
    with torch.no_grad():
        maxk = max(topk)
        batch_size = target.size(0)

        _, pred = output.topk(maxk, 1, True, True)
        pred = pred.t()
        correct = pred.eq(target.view(1, -1).expand_as(pred))

        res = []
        for k in topk:
            correct_k = correct[:k].reshape(-1).float().sum(0, keepdim=True)
            res.append(correct_k.mul_(100.0 / batch_size))
        return res


def get_lr_scheduler(optimizer):
    if cfg.OPTIM.LR_POLICY = "cos"
        scheduler = CosineAnnealingLR(optimizer, cfg.OPTIM.MAX_EPOCH, eta_min=0.00001)
    elif cfg.OPTIM.LR_POLICY == 'steps':
        scheduler = MultiStepLR(
            optimizer, [cfg.OPTIM.MAX_EPOCH * 3 // 7, cfg.OPTIM.MAX_EPOCH * 6 // 7], cfg.OPTIM.LR_MULT=0.1)
    else:
        raise ValueError("not supported")

    return scheduler


def get_meters(is_train=True):
    batch_time = AverageMeter("Time", ":6.3f")
    data_time = AverageMeter("Data", ":5.3f")
    losses = AverageMeter("Loss", ":6.4f")
    top1 = AverageMeter("Acc@1", ":6.3f")
    topk = AverageMeter("Acc@5", ":6.3f")
    return batch_time, data_time, losses, top1, topk


def save_checkpoint(state, is_best, filename="ckpt.pth.tar"):
    path = "./checkpoint/" + filename
    torch.save(state, filename)
    if is_best:
        shutil.copyfile(filename, "ckpt_best.pth.tar")


def show_log(log_str, rank):
    if rank == 0:
        print(log_str)
