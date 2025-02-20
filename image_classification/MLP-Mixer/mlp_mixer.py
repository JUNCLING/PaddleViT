#   Copyright (c) 2021 PPViT Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
This module implements MLP-Mixer
MLP-Mixer: An all-MLP Architecture for Vision
https://arxiv.org/abs/2105.01601
"""

import paddle
import paddle.nn as nn
from droppath import DropPath


class Identity(nn.Layer):
    """ Identity layer

    The output of this layer is the input without any change.
    Use this layer to avoid if condition in some forward methods

    """
    def __init__(self):
        super(Identity, self).__init__()
    def forward(self, x):
        return x


class PatchEmbedding(nn.Layer):
    """Patch Embeddings

    Apply patch embeddings on input images. Embeddings is implemented using a Conv2D op.

    Attributes:
        image_size: int, input image size, default: 224
        patch_size: int, size of patch, default: 4
        in_channels: int, input image channels, default: 3
        embed_dim: int, embedding dimension, default: 96
    """

    def __init__(self, image_size=224, patch_size=4, in_channels=3, embed_dim=96, norm_layer=None):
        super(PatchEmbedding, self).__init__()
        image_size = (image_size, image_size)
        patch_size = (patch_size, patch_size)
        patches_resolution = [image_size[0]//patch_size[0], image_size[1]//patch_size[1]]
        self.image_size = image_size
        self.patch_size = patch_size
        self.patches_resolution = patches_resolution
        self.num_patches = patches_resolution[0] * patches_resolution[1]
        self.in_channels = in_channels
        self.embed_dim = embed_dim
        self.patch_embed = nn.Conv2D(in_channels=in_channels,
                                     out_channels=embed_dim,
                                     kernel_size=patch_size,
                                     stride=patch_size)
        self.norm = norm_layer if norm_layer is not None else Identity()

    def forward(self, x):
        x = self.patch_embed(x) # [batch, embed_dim, h, w] h,w = patch_resolution
        x = x.flatten(start_axis=2, stop_axis=-1) # [batch, embed_dim, h*w] h*w = num_patches
        x = x.transpose([0, 2, 1]) # [batch, h*w, embed_dim]
        x = self.norm(x) # [batch, num_patches, embed_dim]
        return x


class Mlp(nn.Layer):
    """ MLP module

    Impl using nn.Linear and activation is GELU, dropout is applied.
    Ops: fc -> act -> dropout -> fc -> dropout

    Attributes:
        fc1: nn.Linear
        fc2: nn.Linear
        act: GELU
        dropout1: dropout after fc1
        dropout2: dropout after fc2
    """

    def __init__(self, in_features, hidden_features, dropout):
        super(Mlp, self).__init__()
        w_attr_1, b_attr_1 = self._init_weights()
        self.fc1 = nn.Linear(in_features,
                             hidden_features,
                             weight_attr=w_attr_1,
                             bias_attr=b_attr_1)

        w_attr_2, b_attr_2 = self._init_weights()
        self.fc2 = nn.Linear(hidden_features,
                             in_features,
                             weight_attr=w_attr_2,
                             bias_attr=b_attr_2)
        self.act = nn.GELU()
        self.dropout = nn.Dropout(dropout)

    def _init_weights(self):
        weight_attr = paddle.ParamAttr(initializer=paddle.nn.initializer.XavierUniform())
        bias_attr = paddle.ParamAttr(initializer=paddle.nn.initializer.Normal(std=1e-6))
        return weight_attr, bias_attr

    def forward(self, x):
        x = self.fc1(x)
        x = self.act(x)
        x = self.dropout(x)
        x = self.fc2(x)
        x = self.dropout(x)
        return x


class MixerBlock(nn.Layer):
    """Mixer Block

    This block implements Mixer layer which contains 2 MLP blocks and residuals.
    The 1st is token-mixing MLP, the 2nd is channel-mixing MLP.

    Attributes:
        mlp_tokens: Mlp layer for token mixing
        mlp_channels: Mlp layer for channel mixing
        tokens_dim: mlp hidden dim for mlp_tokens
        channels_dim: mlp hidden dim for mlp_channels
        norm1: nn.LayerNorm, apply before mlp_tokens
        norm2: nn.LayerNorm, apply before mlp_channels
    """

    def __init__(self, dim, seq_len, mlp_ratio=(0.5, 4.0), dropout=0., droppath=0.):
        super(MixerBlock, self).__init__()
        tokens_dim = int(mlp_ratio[0] * dim)
        channels_dim = int(mlp_ratio[1] * dim)
        self.norm1 = nn.LayerNorm(dim, epsilon=1e-6)
        self.mlp_tokens = Mlp(seq_len, tokens_dim, dropout=dropout)
        self.drop_path = DropPath(droppath)
        self.norm2 = nn.LayerNorm(dim, epsilon=1e-6)
        self.mlp_channels = Mlp(dim, channels_dim, dropout=dropout)

    def forward(self, x):
        h = x
        x = self.norm1(x)
        x = x.transpose([0, 2, 1])
        x = self.mlp_tokens(x)
        x = x.transpose([0, 2, 1])
        x = self.drop_path(x)
        x = x + h

        h = x
        x = self.norm2(x)
        x = self.mlp_channels(x)
        x = self.drop_path(x)
        x = x + h

        return x


class MlpMixer(nn.Layer):
    """MlpMixer model
    Args:
        num_classes: int, num of image classes, default: 1000
        image_size: int, input image size, default: 224
        in_channels: int, input image channels, default: 3
        patch_size: int, patch size, default: 16
        num_mixer_layers: int, number of mixer blocks, default: 8
        embed_dim: int, output dimension of patch embedding, default: 512
        mlp_ratio: tuple(float, float), mlp scales for mlp token and mlp channels,
                   mlp_tokens hidden dim = mlp_ratio[0] * embed_dim,
                   mlp_channels hidden dim = mlp_ratio[1] * embed_dim,
                   default: (0.5, 4.0)
        dropout: float, dropout rate for mlp, default: 0.
        droppath: float, droppath rate for mixer block, default: 0.
        patch_embed_norm: bool, if True, apply norm in patch embedding, default: False
    """
    def __init__(self,
                 num_classes=1000,
                 image_size=224,
                 in_channels=3,
                 patch_size=16,
                 num_mixer_layers=8,
                 embed_dim=512,
                 mlp_ratio=(0.5, 4.0),
                 dropout=0.,
                 droppath=0.,
                 patch_embed_norm=False):
        super(MlpMixer, self).__init__()
        self.num_classes = num_classes
        self.num_features = embed_dim
        self.embed_dim = embed_dim

        norm_layer = nn.LayerNorm(embed_dim, epsilon=1e-6)
        self.patch_embed = PatchEmbedding(
            image_size=image_size,
            patch_size=patch_size,
            in_channels=in_channels,
            embed_dim=embed_dim,
            norm_layer=norm_layer if patch_embed_norm else None)

        self.mixer_layers = nn.Sequential(
            *[MixerBlock(embed_dim,
                         self.patch_embed.num_patches,
                         mlp_ratio,
                         dropout,
                         droppath) for _ in range(num_mixer_layers)])

        self.norm = nn.LayerNorm(embed_dim, epsilon=1e-6)
        self.head = nn.Linear(embed_dim, self.num_classes)

    def forward_features(self, x):
        x = self.patch_embed(x)
        x = self.mixer_layers(x)
        x = self.norm(x)
        x = x.mean(axis=1)
        return x

    def forward(self, x):
        x = self.forward_features(x)
        x = self.head(x)
        return x


def build_mlp_mixer(config):
    """Build mlp mixer by reading options in config object
    Args:
        config: config instance contains setting options
    Returns:
        model: MlpMixer model
    """

    model = MlpMixer(num_classes=config.MODEL.NUM_CLASSES,
                     image_size=config.DATA.IMAGE_SIZE,
                     in_channels=3,
                     num_mixer_layers=config.MODEL.MIXER.NUM_LAYERS,
                     embed_dim=config.MODEL.MIXER.HIDDEN_SIZE,
                     mlp_ratio=(0.5, 4.0),
                     dropout=config.MODEL.DROPOUT,
                     droppath=config.MODEL.DROPPATH)
    return model
