# -*- coding: utf-8 -*-
# Copyright 2019 Pierre-Luc Delisle. All Rights Reserved.
#
# Licensed under the MIT License;
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://opensource.org/licenses/MIT
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

from typing import List

import numpy as np
import torch
from kerosene.config.trainers import RunConfiguration
from kerosene.training.trainers import ModelTrainer
from kerosene.training.trainers import Trainer
from kerosene.utils.distributed import on_single_device
from kerosene.utils.tensors import flatten, to_onehot
from torch.utils.data import DataLoader

from deepNormalize.config.configurations import DatasetConfiguration
from deepNormalize.inputs.images import SliceType
from deepNormalize.logger.image_slicer import SegmentationSlicer, AdaptedImageSlicer
from deepNormalize.utils.constants import GENERATOR, SEGMENTER, IMAGE_TARGET, EPSILON


class DeepNormalizeTrainer(Trainer):

    def __init__(self, training_config, model_trainers: List[ModelTrainer],
                 train_data_loader: DataLoader, valid_data_loader: DataLoader, run_config: RunConfiguration,
                 dataset_config: DatasetConfiguration):
        super(DeepNormalizeTrainer, self).__init__("DeepNormalizeTrainer", train_data_loader, valid_data_loader,
                                                   model_trainers, run_config)

        self._training_config = training_config
        self._dataset_config = dataset_config
        self._patience_segmentation = training_config.patience_segmentation
        self._seg_slicer = SegmentationSlicer()
        self._slicer = AdaptedImageSlicer()
        self._generator = self._model_trainers[GENERATOR]
        self._segmenter = self._model_trainers[SEGMENTER]

    def train_step(self, inputs, target):
        seg_pred = torch.Tensor().new_zeros(
            size=(self._training_config.batch_size, 1, 32, 32, 32), dtype=torch.float, device="cpu")

        gen_pred = self._generator.forward(inputs)

        if self._should_activate_autoencoder():
            gen_loss = self._generator.compute_train_loss(gen_pred, inputs)
            gen_loss.backward()

            if not on_single_device(self._run_config.devices):
                self.average_gradients(self._generator)

            self._generator.step()
            self._generator.zero_grad()

        if self._should_activate_segmentation():
            seg_pred = self._segmenter.forward(gen_pred)
            seg_loss = self._segmenter.compute_train_loss(torch.nn.functional.softmax(seg_pred, dim=1),
                                                          to_onehot(torch.squeeze(target[IMAGE_TARGET], dim=1).long(),
                                                                    num_classes=4))
            self._segmenter.compute_train_metric(
                torch.argmax(torch.nn.functional.softmax(seg_pred, dim=1), dim=1, keepdim=True), target[IMAGE_TARGET])
            seg_loss.backward()

            if not on_single_device(self._run_config.devices):
                self.average_gradients(self._generator)
                self.average_gradients(self._segmenter)

            self._segmenter.step()
            self._segmenter.zero_grad()

            self._generator.step()
            self._generator.zero_grad()

        if self.current_train_step % 100 == 0:
            self._update_plots(inputs, gen_pred, seg_pred)

        self.custom_variables["Generated Intensity Histogram"] = flatten(gen_pred.cpu())
        self.custom_variables["Input Intensity Histogram"] = flatten(inputs.cpu())

    def validate_step(self, inputs, target):
        gen_pred = self._generator.forward(inputs)

        if self._should_activate_autoencoder():
            self._generator.compute_valid_loss(gen_pred, inputs)

        if self._should_activate_segmentation():
            seg_pred = self._segmenter.forward(gen_pred)
            self._segmenter.compute_valid_loss(torch.nn.functional.softmax(seg_pred, dim=1),
                                               to_onehot(torch.squeeze(target[IMAGE_TARGET], dim=1).long(),
                                                         num_classes=4))
            self._segmenter.compute_valid_metric(
                torch.argmax(torch.nn.functional.softmax(seg_pred, dim=1), dim=1, keepdim=True), target[IMAGE_TARGET])

    def _update_plots(self, inputs, generator_predictions, segmenter_predictions):
        inputs = torch.nn.functional.interpolate(inputs, scale_factor=5, mode="trilinear",
                                                 align_corners=True).cpu().numpy()
        generator_predictions = torch.nn.functional.interpolate(generator_predictions, scale_factor=5, mode="trilinear",
                                                                align_corners=True).cpu().detach().numpy()
        segmenter_predictions = torch.nn.functional.interpolate(
            torch.argmax(torch.nn.functional.softmax(segmenter_predictions, dim=1), dim=1, keepdim=True).float(),
            scale_factor=5, mode="nearest").cpu().detach().numpy()

        inputs = self._normalize(inputs)
        generator_predictions = self._normalize(generator_predictions)
        segmenter_predictions = self._normalize(segmenter_predictions)

        self.custom_variables["Input Batch"] = self._slicer.get_slice(SliceType.AXIAL, inputs)
        self.custom_variables["Generated Batch"] = self._slicer.get_slice(SliceType.AXIAL,
                                                                                      generator_predictions)
        self._custom_variables["Segmented Batch"] = self._seg_slicer.get_colored_slice(SliceType.AXIAL,
                                                                                       segmenter_predictions)

    def scheduler_step(self):
        self._generator.scheduler_step()

        if self._should_activate_segmentation():
            self._segmenter.scheduler_step()

    @staticmethod
    def _normalize(img):
        return (img - np.min(img)) / (np.ptp(img) + EPSILON)

    @staticmethod
    def average_gradients(model):
        size = float(torch.distributed.get_world_size())
        for param in model.parameters():
            torch.distributed.all_reduce(param.grad.data, op=torch.distributed.ReduceOp.SUM)
            param.grad.data /= size

    @staticmethod
    def merge_tensors(tensor_0, tensor_1):
        return torch.cat((tensor_0, tensor_1), dim=0)

    def _should_activate_autoencoder(self):
        return self._current_epoch < self._patience_segmentation

    def _should_activate_segmentation(self):
        return self._current_epoch >= self._patience_segmentation

    def on_epoch_begin(self):
        pass

    def on_epoch_end(self):
        pass
