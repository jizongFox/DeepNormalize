import torch
from kerosene.nn.criterions import CriterionFactory
from torch.nn.modules.loss import _Loss


class MeanLoss(_Loss):
    def __init__(self):
        super(MeanLoss, self).__init__()

    def forward(self, inputs: torch.Tensor, targets: torch.Tensor):
        return torch.mean(inputs)


class CustomCriterionFactory(CriterionFactory):
    def __init__(self):
        super().__init__()
        self.register("MeanLoss", MeanLoss)