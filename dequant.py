# (c) City96 || Apache-2.0 (apache.org/licenses/LICENSE-2.0)
import gguf
import torch
import numpy as np

def dequantize_tensor(tensor, dtype=torch.float16):
    if tensor is None:
        return None

    data = torch.tensor(tensor.data)
    qtype = tensor.tensor_type
    oshape = tensor.tensor_shape

    if qtype == gguf.GGMLQuantizationType.F32:
        return data.to(dtype)
    elif qtype == gguf.GGMLQuantizationType.F16:
        return data.to(dtype)
    elif qtype in dequantize_functions:
        # this is the main pytorch op
        return dequantize(data, qtype, oshape).to(dtype)
    else:
        # this is incredibly slow
        new = gguf.quants.dequantize(data.cpu().numpy(), qtype)
        return torch.from_numpy(new).to(data.device, dtype=dtype)

def dequantize(data, qtype, oshape):
    """
    Dequantize tensor back to usable shape/dtype
    """
    block_size, type_size = gguf.GGML_QUANT_SIZES[qtype]
    dequantize_blocks = dequantize_functions[qtype]

    rows = data.reshape(
        (-1, data.shape[-1])
    ).view(torch.uint8)

    n_blocks = rows.numel() // type_size
    blocks = rows.reshape((n_blocks, type_size))
    blocks = dequantize_blocks(blocks, block_size, type_size)
    return blocks.reshape(oshape)

def to_uint32(x):
    # no uint32 :(
    x = x.view(torch.uint8).to(torch.int32) 
    return (x[:, 0] | x[:, 1] << 8 | x[:, 2] << 16 | x[:, 3] << 24).unsqueeze(1)

def dequantize_blocks_Q8_0(blocks, block_size, type_size):
    d = blocks[:, :2].view(torch.float16)
    x = blocks[:, 2:].view(torch.int8).to(torch.float16)
    return (x * d)

def dequantize_blocks_Q5_0(blocks, block_size, type_size):
    n_blocks = blocks.shape[0]

    d  = blocks[:,  :2]
    qh = blocks[:, 2:6]
    qs = blocks[:, 6: ]

    d = d.view(torch.float16).to(torch.float32)
    qh = to_uint32(qh)

    qh = qh.reshape(n_blocks, 1) >> torch.arange(32, device=d.device, dtype=torch.int32).reshape(1, 32)
    ql = qs.reshape(n_blocks, -1, 1, block_size // 2) >> torch.tensor([0, 4], device=d.device, dtype=torch.uint8).reshape(1, 1, 2, 1)

    qh = (qh & 1).to(torch.uint8)
    ql = (ql & 0x0F).reshape(n_blocks, -1)

    qs = (ql | (qh << 4)).to(torch.int8) - 16
    return (d * qs)

def dequantize_blocks_Q4_0(blocks, block_size, type_size):
    n_blocks = blocks.shape[0]

    d  = blocks[:,  :2].view(torch.float16)
    qs = blocks[:,  2:]

    qs = qs.reshape((n_blocks, -1, 1, block_size // 2)) >> torch.tensor([0, 4], device=d.device, dtype=torch.uint8).reshape((1, 1, 2, 1))
    qs = (qs & 0x0F).reshape((n_blocks, -1)).to(torch.int8) - 8
    return (d * qs)

dequantize_functions = {
    gguf.GGMLQuantizationType.Q8_0: dequantize_blocks_Q8_0,
    gguf.GGMLQuantizationType.Q5_0: dequantize_blocks_Q5_0,
    gguf.GGMLQuantizationType.Q4_0: dequantize_blocks_Q4_0,
}
