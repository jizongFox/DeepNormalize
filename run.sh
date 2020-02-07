#!/bin/bash
CUDA_VISIBLE_DEVICES=0,1 OMP_NUM_THREADS=1 NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=COLL NCCL_LL_THRESHOLD=0 python3 -m torch.distributed.launch --nproc_per_node=2 main.py --config=deepNormalize/config/config_dual_dataset.yaml --use-amp --amp-opt-level=O0 > log.txt