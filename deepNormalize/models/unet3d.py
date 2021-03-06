import torch
import torch.nn as nn
import torch.nn.functional as F


class DoubleConv(nn.Module):
    def __init__(self, in_channels, out_channels, leaky=False):
        super().__init__()
        self.double_conv = nn.Sequential(
            nn.ReplicationPad3d((1, 1, 1, 1, 1, 1)),
            nn.Conv3d(in_channels, out_channels, kernel_size=3, padding=0),
            nn.BatchNorm3d(out_channels),
            nn.ReLU(inplace=True) if leaky is False else nn.LeakyReLU(negative_slope=0.2, inplace=True),
            nn.ReplicationPad3d((1, 1, 1, 1, 1, 1)),
            nn.Conv3d(out_channels, out_channels, kernel_size=3, padding=0),
            nn.BatchNorm3d(out_channels),
            nn.ReLU(inplace=True) if leaky is False else nn.LeakyReLU(negative_slope=0.2, inplace=True)
        )

    def forward(self, x):
        return self.double_conv(x)


class Down(nn.Module):
    """Downscaling with maxpool then double conv"""

    def __init__(self, in_channels, out_channels, leaky=False):
        super().__init__()
        self.maxpool_conv = nn.Sequential(
            nn.MaxPool3d(2),
            DoubleConv(in_channels, out_channels, leaky=leaky)
        )

    def forward(self, x):
        return self.maxpool_conv(x)


class Up(nn.Module):
    """Upscaling then double conv"""

    def __init__(self, in_channels, out_channels, interpolate=False, leaky=False):
        super().__init__()

        if interpolate:
            self.up = nn.Upsample(scale_factor=2, mode='nearest')
        else:
            self.up = nn.ConvTranspose3d(in_channels // 2, in_channels // 2, kernel_size=2, stride=2)

        self.conv = DoubleConv(in_channels, out_channels, leaky=leaky)

    def forward(self, x1, x2):
        x1 = self.up(x1)

        # input is BxCxDxHxW
        diffZ = x2.size()[2] - x1.size()[2]
        diffY = x2.size()[3] - x1.size()[3]
        diffX = x2.size()[4] - x1.size()[4]

        x1 = F.pad(x1, [diffZ // 2, diffZ - diffZ // 2,
                        diffX // 2, diffX - diffX // 2,
                        diffY // 2, diffY - diffY // 2], mode="replicate")

        x = torch.cat([x2, x1], dim=1)
        return self.conv(x)


class OutConv(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(OutConv, self).__init__()
        self.conv = nn.Conv3d(in_channels, out_channels, kernel_size=1)

    def forward(self, x):
        return self.conv(x)


class Unet(nn.Module):
    def __init__(self, in_channels, out_channels, interpolate=False, leaky=False):
        super(Unet, self).__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.interpolate = interpolate

        self.inc = DoubleConv(in_channels, 64)
        self.down1 = Down(64, 128, leaky)
        self.down2 = Down(128, 256, leaky)
        self.down3 = Down(256, 512, leaky)
        self.down4 = Down(512, 512, leaky)
        self.up1 = Up(1024, 256, interpolate, leaky)
        self.up2 = Up(512, 128, interpolate, leaky)
        self.up3 = Up(256, 64, interpolate, leaky)
        self.up4 = Up(128, 64, interpolate, leaky)
        self.out_conv = OutConv(64, out_channels)

        self.encoder_frozen = False

        for m in self.modules():
            if isinstance(m, (nn.Conv3d, nn.ConvTranspose3d)):
                nn.init.kaiming_uniform_(m.weight, mode='fan_out')
            elif isinstance(m, nn.BatchNorm3d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

    def freeze_encoder(self):
        for encoder_layer in [self.inc, self.down1, self.down2, self.down3, self.down4]:
            for param in encoder_layer.parameters():
                param.requires_grad = False

        self.encoder_frozen = True

    def unfreeze_encoder(self):
        for encoder_layer in [self.inc, self.down1, self.down2, self.down3, self.down4]:
            for param in encoder_layer.parameters():
                param.requires_grad = True

        self.encoder_frozen = False

    def forward(self, x):
        x1 = self.inc(x)
        x2 = self.down1(x1)
        x3 = self.down2(x2)
        x4 = self.down3(x3)
        x5 = self.down4(x4)
        x = self.up1(x5, x4)
        x = self.up2(x, x3)
        x = self.up3(x, x2)
        x = self.up4(x, x1)

        return self.out_conv(x)
