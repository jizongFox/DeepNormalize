#!/bin/bash
CUDA_VISIBLE_DEVICES=0,1 OMP_NUM_THREADS=1 python3 -m torch.distributed.launch --nproc_per_node=2 main.py --config=deepNormalize/config/config.yaml --use-amp --amp-opt-level=O1 > log.txt