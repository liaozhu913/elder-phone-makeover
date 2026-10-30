# 来源与许可说明

本技能（elder-phone-makeover）基于工作区自建项目「项目-0041 安卓适老化桌面」的成品整理，
用于把一部普通安卓手机改造成适合老人使用的大字桌面。

## 代码与文档

- 本仓库的 `SKILL.md`、`scripts/`、`references/` 及各文档由项目作者编写，采用 **MIT 许可**（见 `LICENSE`）。
- 成品 APK `assets/elder-launcher.apk` 为 **debug 签名**，仅供体验，包名为 `com.elderhome.launcher.debug`。
  若手机里装过不同签名的同名包，覆盖安装可能报签名冲突，处理见 `references/05_故障排查.md`。

## 第三方资源与署名

- **界面图标**：采用 Google **Material Symbols**（Rounded），按 **Apache License 2.0** 使用。
  - 来源：https://github.com/google/material-design-icons
  - 许可全文：https://www.apache.org/licenses/LICENSE-2.0
  - 本技能仅将官方 SVG 图标转换为 Android vector drawable 以适配界面，未改变图形语义。
- **天气数据**：来自**中国天气网**（weather.com.cn）的公开接口，数据版权归中国气象局／中国天气网所有。
  本技能仅作界面展示，不提供数据服务、不缓存转售；天气准确性以官方为准。
- 除天气外本技能无联网行为，不上传任何数据、无统计遥测。详见 `references/04_兼容与限制.md`。

## 上游与致谢

- 桌面成品源自本工作区自建项目「项目-0041 本地客户端-安卓适老化桌面」，**非第三方开源项目**。
- 未移植其他开源桌面项目（如 cleanyourtv、CleanFrame 等）的代码或资源；如界面思路有借鉴，
  仅为设计层面的参考。

## 隐私

- 本仓库不含任何真实用户信息、手机号、序列号、局域网地址、账号或密钥。
- 预览图为脱敏真机截图，仅含测试占位内容。
