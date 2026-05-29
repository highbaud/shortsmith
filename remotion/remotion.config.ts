import { Config } from "@remotion/cli/config";

// PNG (lossless) intermediate frames — JPEG frames produce 8x8 block/mosquito
// artifacts on the high-contrast stroked caption text.
Config.setVideoImageFormat("png");
Config.setOverwriteOutput(true);
// H.264 + yuv420p so the output plays everywhere (IG/TikTok/Reels).
Config.setCodec("h264");
Config.setPixelFormat("yuv420p");
Config.setCrf(18);
