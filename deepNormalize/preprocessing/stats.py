import logging
import logging
import multiprocessing

import abc
import nibabel as nib
import numpy as np
import os
import pandas
from samitorch.inputs.patch import Patch, CenterCoordinate
from samitorch.inputs.transformers import ToNumpyArray, ApplyMask, \
    ResampleNiftiImageToTemplate, LoadNifti, PadToPatchShape, RemapClassIDs
from samitorch.utils.files import extract_file_paths
from samitorch.utils.slice_builder import SliceBuilder
from torchvision.transforms import transforms

from deepNormalize.utils.utils import natural_sort

logging.basicConfig(level=logging.INFO)

LABELSFORTESTING = 0
LABELSFORTRAINNG = 1
ROIT1 = 2
T1 = 3
T1_1MM = 4
T1_IR = 5
T2_FLAIR = 6


class AbstractPreProcessingPipeline(metaclass=abc.ABCMeta):
    """
    Define a preprocessing pipeline.
    """

    @staticmethod
    def _get_image_affine(file):
        return nib.load(file).affine

    @staticmethod
    def _get_image_header(file):
        return nib.load(file).header

    @abc.abstractmethod
    def run(self, **kwargs):
        """
        Run the preprocessing pipeline.
        Args:
            **kwargs: Optional keyword arguments.
        """
        raise NotImplementedError

    @staticmethod
    def chunks(l, n):
        return [l[i:i + n] for i in range(0, len(l), n)]

    def _do_job(self, *args):
        raise NotImplementedError

    def _dispatch_jobs(self, files, job_number):
        total = len(files)
        chunk_size = int(total / job_number)
        slices = iSEGPipeline.chunks(files, chunk_size)
        jobs = []

        for slice in slices:
            j = multiprocessing.Process(target=self._do_job, args=(slice,))
            jobs.append(j)
        for j in jobs:
            j.start()

    @staticmethod
    def get_patches_from_sample(sample, patch_size, step, keep_foreground_only: bool = True, keep_labels: bool = True):
        slices = SliceBuilder(sample.x.shape, patch_size=patch_size, step=step).build_slices()

        patches = list()

        for slice in slices:
            if keep_labels:
                center_coordinate = CenterCoordinate(sample.x[tuple(slice)], sample.y[tuple(slice)])
                patches.append(Patch(slice, 0, center_coordinate))
            else:
                patches.append(Patch(slice, 0, None))

        if keep_foreground_only:
            return np.array(list(filter(lambda patch: patch.center_coordinate.is_foreground, patches)))
        else:
            return patches

    @staticmethod
    def get_patches(image, patch_size, step):
        slices = SliceBuilder(image.shape, patch_size=patch_size, step=step).build_slices()

        patches = list()

        for slice in slices:
            patches.append(Patch(slice, 0, None))

        return patches

    @staticmethod
    def get_filtered_patches(image, label, patch_size, step):
        slices = SliceBuilder(image.shape, patch_size=patch_size, step=step).build_slices()

        patches = list()

        for slice in slices:
            center_coordinate = CenterCoordinate(image[tuple(slice)], label[tuple(slice)])
            patches.append(Patch(image[tuple(slice)], 0, center_coordinate))

        return list(filter(lambda patch: patch.center_coordinate.is_foreground, patches))


class iSEGPipeline(AbstractPreProcessingPipeline):
    LOGGER = logging.getLogger("iSEGPipeline")
    PATCH_SIZE = (1, 32, 32, 32)
    STEP = (1, 4, 4, 4)

    def __init__(self, root_dir):
        self._root_dir = root_dir

    def run(self):
        images_T1 = natural_sort(extract_file_paths(os.path.join(self._root_dir, "T1")))
        labels = natural_sort(extract_file_paths(os.path.join(self._root_dir, "label")))
        files = np.stack((np.array(images_T1), np.array(labels)), axis=1)

        # self._dispatch_jobs(files, 5)
        self._do_job(files)

    def _do_job(self, files):
        patches = list()

        for file in files:
            self.LOGGER.info("Processing file {}".format(file[1]))
            label = self._to_numpy_array(file[1])
            label = self._remap_class_ids(label)
            self.LOGGER.info("Processing file {}".format(file[0]))
            t1 = self._to_numpy_array(file[0])
            patches.append(self._extract_patches(t1, label, self.PATCH_SIZE, self.STEP))

        patches_np = list()
        for image in patches:
            patches_np.append(list(map(lambda patch: patch.slice, image)))

        patches = [item for sublist in patches_np for item in sublist]

        dataset_mean = np.mean([np.mean(patch) for patch in patches])

        std = np.sqrt(np.array([(((patch - dataset_mean)**2).mean()) for patch in patches]).mean())

        print("Mean: {}".format(np.mean(dataset_mean)))
        print("Std: {}".format(np.mean(np.array(std))))

    def _to_numpy_array(self, file):
        transform_ = transforms.Compose([ToNumpyArray()])

        return transform_(file)

    def _remap_class_ids(self, file):
        transform_ = transforms.Compose([RemapClassIDs([10, 150, 250], [1, 2, 3])])
        return transform_(file)

    def _extract_patches(self, image, label, patch_size, step):
        transforms_ = transforms.Compose([PadToPatchShape(patch_size=patch_size, step=step)])
        transformed_image = transforms_(image)
        transformed_label = transforms_(label)

        return iSEGPipeline.get_filtered_patches(transformed_image, transformed_label, patch_size, step)


class MRBrainSPipeline(AbstractPreProcessingPipeline):
    LOGGER = logging.getLogger("MRBrainSPipeline")
    PATCH_SIZE = (1, 32, 32, 32)
    STEP = (1, 4, 4, 4)

    def __init__(self, root_dir):
        self._root_dir = root_dir

    def run(self):
        source_paths = list()

        for subject in sorted(os.listdir(os.path.join(self._root_dir))):
            source_paths.append(extract_file_paths(os.path.join(self._root_dir, subject)))

        # self._dispatch_jobs(source_paths, 5)
        self._do_job(source_paths)

    def _do_job(self, files):
        patches = list()
        for file in files:
            self.LOGGER.info("Processing file {}".format(file[LABELSFORTESTING]))
            label_for_testing = self._resample_to_template(file[LABELSFORTESTING], file[T1_1MM],
                                                           interpolation="linear")
            label_for_testing = self._to_numpy_array(label_for_testing)
            label_for_testing = label_for_testing.transpose((3, 0, 1, 2))
            label_for_testing = np.rot90(label_for_testing, axes=(1, -2))

            self.LOGGER.info("Processing file {}".format(file[T1]))
            t1 = self._resample_to_template(file[T1], file[T1_1MM], interpolation="continuous")
            t1 = self._to_numpy_array(t1)
            t1 = t1.transpose((3, 0, 1, 2))
            t1 = np.rot90(t1, axes=(1, -2))
            t1 = self._apply_mask(t1, label_for_testing)
            patches.append(self._extract_patches(t1, label_for_testing, self.PATCH_SIZE, self.STEP))

        patches_np = list()
        for image in patches:
            patches_np.append(list(map(lambda patch: patch.slice, image)))

        patches = [item for sublist in patches_np for item in sublist]

        dataset_mean = np.mean([np.mean(patch) for patch in patches])

        std = np.sqrt(np.array([(((patch - dataset_mean) ** 2).mean()) for patch in patches]).mean())

        print("Mean: {}".format(np.mean(dataset_mean)))
        print("Std: {}".format(np.mean(std)))

    def _pad_to_shape(self, image, patch_size, step):
        transforms_ = transforms.Compose([PadToPatchShape(patch_size=patch_size, step=step)])

        return transforms_(image)

    def _to_numpy_array(self, file):
        transform_ = transforms.Compose([ToNumpyArray()])

        return transform_(file)

    def _resample_to_template(self, file, nifti_template, interpolation):
        transforms_ = transforms.Compose([LoadNifti(),
                                          ResampleNiftiImageToTemplate(clip=False,
                                                                       template=nifti_template,
                                                                       interpolation=interpolation)])
        return transforms_(file)

    def _apply_mask(self, file, mask):
        transform_ = transforms.Compose([ApplyMask(mask)])

        return transform_(file)

    def _extract_patches(self, image, label, patch_size, step):
        transforms_ = transforms.Compose([PadToPatchShape(patch_size=patch_size, step=step)])
        transformed_image = transforms_(image)
        transformed_label = transforms_(label)

        return ABIDEPreprocessingPipeline.get_filtered_patches(transformed_image, transformed_label, patch_size, step)


class ABIDEPreprocessingPipeline(AbstractPreProcessingPipeline):
    """
    An ABIDE data pre-processing pipeline. Extract necessary tissues for brain segmentation among other transformations.
    """
    LOGGER = logging.getLogger("PreProcessingPipeline")
    PATCH_SIZE = (1, 32, 32, 32)
    STEP = (1, 4, 4, 4)

    def __init__(self, csv_path):
        """
        Pre-processing pipeline constructor.
        Args:
            root_dir: Root directory where all files are located.
        """
        self._csv_path = csv_path

    def run(self, prefix: str = ""):
        files = pandas.read_csv(self._csv_path)
        images_T1 = np.asarray(files["T1"])
        labels = np.asarray(files["labels"])

        files = np.stack((np.array(images_T1), np.array(labels)), axis=1)

        self._do_job(files)

    def _do_job(self, files):
        patches = list()
        for file in files:
            self.LOGGER.info("Processing file {}".format(file[1]))
            label = self._to_numpy_array(file[1])
            self.LOGGER.info("Processing file {}".format(file[0]))
            t1 = self._to_numpy_array(file[0])
            patches.append(self._extract_patches(t1, label, self.PATCH_SIZE, self.STEP))

        patches_np = list()
        for image in patches:
            patches_np.append(list(map(lambda patch: patch.slice, image)))

        patches = [item for sublist in patches_np for item in sublist]

        dataset_mean = np.mean([np.mean(patch) for patch in patches])

        std = np.sqrt(np.array([(((patch - dataset_mean) ** 2).mean()) for patch in patches]).mean())

        print("Mean: {}".format(np.mean(dataset_mean)))
        print("Std: {}".format(np.mean(std)))

    def _to_numpy_array(self, file):
        transform_ = transforms.Compose([ToNumpyArray()])

        return transform_(file)

    def _extract_patches(self, image, label, patch_size, step):
        transforms_ = transforms.Compose([PadToPatchShape(patch_size=patch_size, step=step)])
        transformed_image = transforms_(image)
        transformed_label = transforms_(label)

        return ABIDEPreprocessingPipeline.get_filtered_patches(transformed_image, transformed_label, patch_size, step)


if __name__ == "__main__":
    # iSEGPipeline(args.path_iseg, "/data/users/pldelisle/datasets/Preprocessed_4/iSEG/Training",
    #              step=(1, 4, 4, 4)).run()
    # MRBrainSPipeline(args.path_mrbrains, "/data/users/pldelisle/datasets/Preprocessed_4/MRBrainS/DataNii/TrainingData",
    #                  step=(1, 4, 4, 4)).run()
    # iSEGPipeline(args.path_iseg, "/data/users/pldelisle/datasets/Preprocessed_8/iSEG/Training",
    #              step=(1, 8, 8, 8)).run()
    # MRBrainSPipeline(args.path_mrbrains, "/data/users/pldelisle/datasets/Preprocessed_8/MRBrainS/DataNii/TrainingData",
    #                  step=(1, 8, 8, 8)).run()
    # iSEGPipeline("/mnt/md0/Data/Direct/iSEG/Training").run()
    # MRBrainSPipeline("/mnt/md0/Data/Direct/iSEG/Training").run()
    ABIDEPreprocessingPipeline("/home/pierre-luc-delisle/ABIDE/5.1/output_abide_images.csv").run()
    # iSEGPipeline(args.path_iseg, "/mnt/md0/Data/Preprocessed_augmented/iSEG/Training",
    #              step=None, do_extract_patches=False, augment=True).run()
    # MRBrainSPipeline(args.path_mrbrains, "/mnt/md0/Data/Preprocessed_augmented/MRBrainS/DataNii/TrainingData",
    #                  step=None, do_extract_patches=False, augment=True).run()
