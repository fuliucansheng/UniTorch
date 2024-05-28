# Copyright (c) FULIUCANSHENG.
# Licensed under the MIT License.

import os
import torch
import json
import numpy as np
from PIL import Image, ImageFilter
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from transformers import CLIPTokenizer
from torchvision.transforms.functional import crop
from torchvision.transforms import (
    Resize,
    CenterCrop,
    ToTensor,
    Normalize,
    Compose,
    RandomHorizontalFlip,
)
from diffusers.image_processor import VaeImageProcessor
from unitorch.models import HfTextClassificationProcessor, GenericOutputs


class ControlNetXLProcessor:
    def __init__(
        self,
        vocab1_path: str,
        merge1_path: str,
        vocab2_path: str,
        merge2_path: str,
        vae_config_path: str,
        max_seq_length: Optional[int] = 77,
        position_start_id: Optional[int] = 0,
        pad_token1: Optional[str] = "<|endoftext|>",
        pad_token2: Optional[str] = "!",
        image_size: Optional[int] = 1024,
    ):
        tokenizer1 = CLIPTokenizer(
            vocab_file=vocab1_path,
            merges_file=merge1_path,
        )

        tokenizer1.cls_token = tokenizer1.bos_token
        tokenizer1.sep_token = tokenizer1.eos_token
        tokenizer1.pad_token = pad_token1

        self.text_processor1 = HfTextClassificationProcessor(
            tokenizer=tokenizer1,
            max_seq_length=max_seq_length,
            position_start_id=position_start_id,
        )

        tokenizer2 = CLIPTokenizer(
            vocab_file=vocab2_path,
            merges_file=merge2_path,
        )

        tokenizer2.cls_token = tokenizer2.bos_token
        tokenizer2.sep_token = tokenizer2.eos_token
        tokenizer2.pad_token = pad_token2

        self.text_processor2 = HfTextClassificationProcessor(
            tokenizer=tokenizer2,
            max_seq_length=max_seq_length,
            position_start_id=position_start_id,
        )

        self.vision_processor = Compose(
            [
                Resize(image_size),
                CenterCrop(image_size),
                ToTensor(),
                Normalize([0.5], [0.5]),
            ]
        )
        self.condition_vision_processor = Compose(
            [
                Resize(image_size),
                CenterCrop(image_size),
                ToTensor(),
            ]
        )

        vae_config_dict = json.load(open(vae_config_path))
        vae_scale_factor = 2 ** (len(vae_config_dict.get("block_out_channels", [])) - 1)
        self.vae_image_processor = VaeImageProcessor(
            vae_scale_factor=vae_scale_factor, do_convert_rgb=True
        )
        self.vae_mask_image_processor = VaeImageProcessor(
            vae_scale_factor=vae_scale_factor
        )
        self.vae_condition_image_processor = VaeImageProcessor(
            vae_scale_factor=vae_scale_factor,
            do_convert_rgb=True,
            do_normalize=False,
        )

    def text2image(
        self,
        image: Union[Image.Image, str],
        condition_image: Union[Image.Image, str],
        prompt: str,
        prompt2: Optional[str] = None,
        max_seq_length: Optional[int] = None,
    ):
        if isinstance(image, str):
            image = Image.open(image)
        image = image.convert("RGB")
        if isinstance(condition_image, str):
            condition_image = Image.open(condition_image)
        condition_image = condition_image.convert("RGB")

        original_size = image.size
        image = self.vision_resize(image)
        if self.center_crop:
            y1 = max(0, int(round((image.height - self.image_size) / 2.0)))
            x1 = max(0, int(round((image.width - self.image_size) / 2.0)))
            image = self.vision_crop(image)
        else:
            y1, x1, h, w = self.vision_crop.get_params(
                image, (self.image_size, self.image_size)
            )
            image = crop(image, y1, x1, h, w)
        if self.vision_flip:
            x1 = image.width - x1
            image = self.vision_flip(image)
        crop_top_left = (y1, x1)
        pixel_values = self.vision_processor(image)

        add_time_ids = (
            original_size + crop_top_left + [self.image_size, self.image_size]
        )

        condition_pixel_values = self.condition_vision_processor(condition_image)

        prompt2 = prompt2 or prompt
        prompt_outputs = self.text_processor1.classification(
            prompt, max_seq_length=max_seq_length
        )
        prompt2_outputs = self.text_processor2.classification(
            prompt2, max_seq_length=max_seq_length
        )

        return GenericOutputs(
            pixel_values=pixel_values,
            condition_pixel_values=condition_pixel_values,
            input_ids=prompt_outputs.input_ids,
            attention_mask=prompt_outputs.attention_mask,
            input2_ids=prompt2_outputs.input_ids,
            attention2_mask=prompt2_outputs.attention_mask,
            add_time_ids=torch.tensor(add_time_ids),
        )

    def text2image_inputs(
        self,
        condition_image: Union[Image.Image, str],
        prompt: str,
        prompt2: Optional[str] = None,
        negative_prompt: Optional[str] = "",
        negative_prompt2: Optional[str] = None,
        max_seq_length: Optional[int] = None,
    ):
        if isinstance(condition_image, str):
            condition_image = Image.open(condition_image)
        condition_image = condition_image.convert("RGB")

        condition_pixel_values = self.vae_condition_image_processor.preprocess(
            condition_image
        )[0]

        prompt_outputs = self.text_processor1.classification(
            prompt, max_seq_length=max_seq_length
        )
        prompt2_outputs = self.text_processor2.classification(
            prompt2, max_seq_length=max_seq_length
        )
        negative_prompt_outputs = self.text_processor1.classification(
            negative_prompt, max_seq_length=max_seq_length
        )
        negative_prompt2_outputs = self.text_processor2.classification(
            negative_prompt2, max_seq_length=max_seq_length
        )

        return GenericOutputs(
            condition_pixel_values=condition_pixel_values,
            input_ids=prompt_outputs.input_ids,
            attention_mask=prompt_outputs.attention_mask,
            input2_ids=prompt2_outputs.input_ids,
            attention2_mask=prompt2_outputs.attention_mask,
            negative_input_ids=negative_prompt_outputs.input_ids,
            negative_attention_mask=negative_prompt_outputs.attention_mask,
            negative_input2_ids=negative_prompt2_outputs.input_ids,
            negative_attention2_mask=negative_prompt2_outputs.attention_mask,
        )

    def image2image_inputs(
        self,
        prompt: str,
        condition_image: Union[Image.Image, str],
        image: Union[Image.Image, str],
        prompt2: Optional[str] = None,
        negative_prompt: Optional[str] = "",
        negative_prompt2: Optional[str] = None,
        max_seq_length: Optional[int] = None,
    ):
        if isinstance(image, str):
            image = Image.open(image)
        image = image.convert("RGB")

        pixel_values = self.vae_image_processor.preprocess(image)[0]

        text_inputs = self.text2image_inputs(
            condition_image=condition_image,
            prompt=prompt,
            prompt2=prompt2,
            negative_prompt=negative_prompt,
            negative_prompt2=negative_prompt2,
            max_seq_length=max_seq_length,
        )
        return GenericOutputs(
            pixel_values=pixel_values,
            **text_inputs,
        )

    def inpainting_inputs(
        self,
        image: Union[Image.Image, str],
        mask_image: Union[Image.Image, str],
        condition_image: Union[Image.Image, str],
        prompt: str,
        prompt2: Optional[str] = None,
        negative_prompt: Optional[str] = "",
        negative_prompt2: Optional[str] = None,
        max_seq_length: Optional[int] = None,
    ):
        if isinstance(image, str):
            image = Image.open(image)
        image = image.convert("RGB")

        if isinstance(mask_image, str):
            mask_image = Image.open(mask_image)
        mask_image = mask_image.convert("L")

        pixel_values = self.vae_image_processor.preprocess(image)[0]
        pixel_masks = self.vae_mask_image_processor.preprocess(mask_image)[0]
        pixel_masks = (pixel_masks + 1) / 2

        text_inputs = self.text2image_inputs(
            condition_image=condition_image,
            prompt=prompt,
            prompt2=prompt2,
            negative_prompt=negative_prompt,
            negative_prompt2=negative_prompt2,
            max_seq_length=max_seq_length,
        )
        return GenericOutputs(
            pixel_values=pixel_values,
            pixel_masks=pixel_masks,
            **text_inputs,
        )
