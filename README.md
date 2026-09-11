# 滑块自动登录（纯视觉版）

[![Build & Release](https://github.com/Entruv1/AutoLogin/actions/workflows/build.yml/badge.svg)](https://github.com/Entruv1/AutoLogin/actions/workflows/build.yml)
[![Release](https://img.shields.io/github/v/release/Entruv1/AutoLogin?display_name=tag)](https://github.com/Entruv1/AutoLogin/releases/latest)

针对**某些网站**（需滑块验证登录的内网/后台系统）的自动登录工具。纯视觉实现：
只截屏、只动鼠标键盘，不读 DOM、不注入 JS、不调接口。



## 工作原理

```
[一条龙] 把登录页弄到前台（已在 → 切窗口 → 命令行打开）
     → 截屏 → 视觉定位登录按钮 → 填表（点击聚焦 + Tab 切框 + 填完自证）→ 点登录 → 等弹窗
     → 截图片面板 → YOLOv8-seg 找缺口 → 拟人轨迹拖动 → 视觉校验 → 失败刷新重试
```

按一次热键就是完整一条龙：**打开网页 → 切到前台 → 自动登录**，不用先自己把浏览器摆好。

七个关键点：

1. **登录表单**：靠"橙色实心圆角条"这一颜色+形状特征锁定登录按钮，其余控件按该站
   固定布局的相对偏移推出。偏移量已在 1366×768 / 1600×900 / 1920×1080 三种视口下
   实测为常量；按钮高度用来反推浏览器缩放比例。
2. **滑块弹窗**：以**相对"点登录前那一帧"的帧差**为主判据 —— 它不关心页面压暗多少、
   也不关心遮罩怎么实现，弹窗一出现就一定有帧差。再配一条"画面里最大的纯白连通区域"
   作为候选（弹窗被压暗的页面衬托时最准）。两条路径都必须通过结构校验：
   面板是照片纹理（拉普拉斯能量）+ 正下方有一条"白底带深色字符"的滑轨条。
3. **拖动距离**：拼图块内嵌在滑块按钮里并与其 1:1 同步移动，所以
   `拖动距离 = 缺口左边缘 − 拼图块左边缘`，两个量在同一张截图上量出即可，
   不需要知道任何缩放比例或接口参数。
4. **焦点判据**：输入框聚焦时描边会变成 `#409EFF` 蓝（实测是 1px 的完整矩形描边，
   BGR `252,135,65`），未聚焦是浅灰 `#DCDFE6`。于是"哪一圈描边是蓝的"就等于
   "焦点在哪个框" —— 这是后面一切按键操作的安全前提：**绝不对一个看不见的框盲发按键**。
5. **移动焦点优先用 Tab，而不是点击**：Tab 只动焦点、不产生点击，所以完全不会被
   盖在输入框上的浮层吃掉；而且焦点一离开原来的框，浮层就自己收起来了。
   每切换一步都用聚焦描边复核，跑过头就 `Shift+Tab` 退回来，Tab 不灵再退回"点击+复核"。
6. **填表自证**：填完立刻截屏回读每个输入框（"框里明显比框底更暗的像素占比"），
   并确认登录按钮没被挡住。空框里的浅灰占位符（灰度 ~212）不会被误当成内容。

## 环境要求

- Windows，Python **3.14**（需带 tkinter，且 cv2 / onnxruntime / shapely / pystray 可用）。
- 桌面缩放 100%（其他缩放也能用，会自动换算）。

依赖装一遍即可：

```bat
pip install -r requirements.txt
```

## 使用

### 直接用（推荐）

到 [Releases](https://github.com/Entruv1/AutoLogin/releases/latest) 下载
`auto_login_vision.exe` 和 `config.example.json`，放在同一个目录，
把后者改名成 `config.json` 填好账号，双击 exe 即可 —— **不需要装 Python**。

### 跑源码

双击 `run.bat`：托盘出现图标后，把浏览器打开到登录页、窗口留在前台，
按 `Ctrl+Alt+L` 即自动完成登录。

命令行调试：

```bat
python src\main.py --probe    :: 只截屏做视觉定位并输出调试图，不碰鼠标键盘
python src\main.py --login    :: 不开界面，直接跑一次登录
python src\main.py --selftest :: 打包后自检（截屏 + 模型加载），结果写 _debug/selftest.txt
python src\main.py            :: 托盘常驻
```

### 打包

本地：双击 `build.bat`，产物在 `dist\auto_login_vision.exe`。

云端：推 `v*` 标签即自动出 Release（见下）。

## 自动构建与发布

`.github/workflows/build.yml` 定义了三件事：

| 触发 | 行为 |
|---|---|
| 推到 `main` / 提 PR | 装依赖 → 语法自检 → 打 exe → 产物存为 Artifact（30 天） |
| 推 `v*` 标签 | 同上，并**自动创建 GitHub Release**，附上 exe 与 `config.example.json` |
| 手动 `workflow_dispatch` | 在 Actions 页面随时手点一次构建 |

发版就是打个标签：

```bat
git tag v1.0.1
git push origin v1.0.1
```

等 Actions 跑完，Release 页面就会出现带 exe 的新版本。
构建里加了一道体积校验（< 80MB 直接失败），防止模型或 onnxruntime 没打进去
却“构建成功”地把空壳发出去。

## 热键

两层热键，互不干扰：

| 类型 | 触发对象 | 说明 |
|---|---|---|
| **全局热键** | 当前账号 | 托盘菜单里带 `●` 的那个，可以在托盘「切换账号」里换 |
| **账号独立热键** | 该账号 | 每个账号预设可以各配一个；按下时自动切到该账号再登录 |

- 账号的独立热键**留空**就表示"这个账号不需要单独热键"，只用全局热键或托盘菜单。
- 同一个组合键被两个账号占用时，只保留排在前面的那个，冲突会写进 `login.log`。
- 日志启动时会打印一行 `已注册热键：CTRL+ALT+2、CTRL+ALT+L`，托盘提示也会带上。

### 设置热键不用手打

设置界面里每个热键右边都有 **「按下设置」** —— 点一下，然后直接按你要用的组合键，
按完即记录（`Esc` 取消）。

- 必须带 `Ctrl` / `Alt` / `Shift` / `Win`，或者单独用一个 `F1`~`F24`
  —— 不然一个裸字母就会把全系统的输入抢走。
- 录制期间登录触发会临时挂起，万一录到已注册的组合键也不会顺手跑一遍登录。
- 支持字母、数字、功能键、方向键、`Home`/`End`/`PageUp` 等、小键盘、常用标点。

## 配置

首次运行在 exe（或项目根目录）生成 `config.json`。仓库里带了一份
`config.example.json` 作为模板，把它复制成 `config.json` 再改即可：

```bat
copy config.example.json config.json
```

字段说明（下例是占位示例，不是真实站点）：

```json
{
  "url": "http://example.com/#/login",
  "hotkey": "ctrl+alt+l",
  "active": 0,
  "retries": 3,
  "window_title": "示例站点",
  "auto_open": true,
  "accounts": [
    { "label": "账号一", "username": "user1", "password": "<base64>",
      "org": "某某单位", "hotkey": "ctrl+alt+1" },
    { "label": "备用账号", "username": "user2", "password": "<base64>",
      "org": "某某单位", "hotkey": "" }
  ]
}
```

`window_title` 是浏览器窗口标题里应包含的关键字 —— 触发前会先把含该关键字的窗口
切到前台，所以浏览器被别的窗口挡住也能用。本站点在登录页时的窗口标题形如
`某某系统 和另外 N 个页面 - 个人 - Microsoft Edge`，所以这里填一个**只在登录页会出现**
的关键字（如站点名），它同时兼作"这个窗口当前就在登录页"的判据（用来判断无需新开标签页），
登录成功后标题会变成"首页"，就匹配不上了。

`auto_open` 控制是否开启"一条龙"（默认 `true`）：已开着登录页就直接用、窗口不在
登录页就命令行打开（落成**新标签页**）、浏览器没开就启动它。设为 `false` 则退化成
"只把含关键字的窗口切到前台"，完全不碰浏览器别的状态。

> **为什么是"新标签页"而不是"原地跳转"？**
> 原地跳转（`Ctrl+L` 改地址）虽然更省标签，但它会**顶掉用户当前正在看的那一页** ——
> 开发时实测把用户正在浏览的页面换掉了。所以这里选择"只增不改"：
> 宁可偶尔多一个标签页，也不覆盖用户的页面。
> 代价是：如果浏览器停在首页、并且会话还没过期，再按一次热键会多开一个标签页
> （登录页的窗口标题含站点关键字，登录成功后变成"首页"，就匹配不上了）。

也可以右键托盘图标 → `设置…` 图形化修改：

- 账号预设：**新增 / 删除 / 保存此账号**，每个账号可带独立热键；
- **保存不会关窗** —— 加完一个账号可以接着加下一个，不用反复开设置；
  切账号、加/删账号、关窗时都会自动落盘，不会有填了一半丢掉的坑；
- 全局热键、滑块最大重试、窗口标题关键字、**一条龙开关**；
- **测试视觉定位** 按钮（只截屏识别、不动鼠标，把结果画框存到 `_debug/`）。

密码只做 base64 混淆，**不是加密**，防止旁人一眼看到而已。

## 排错

- 出错时 `_debug/` 下会有带框的截图和 `login.log`，对着看是哪一步没对上。
- `baseline.png`：点登录之前的画面，后面所有判断都以它为基准。
- `filled.png`：填完表之后的画面 —— 先看这里确认三项是不是填对了框。
- `fill_retry_*.png`：填表自证没通过、准备重填时的画面。
- 输入框**描边是蓝色** = 焦点在这个框；填表日志里每一步都会报焦点落在哪个框，
  对不上时看 `login.log` 就知道是"Tab 切框"还是"点击兜底"生效了。
- `fail_no_form.png`：屏幕上没找到登录页 —— 通常是页面没加载完、或者浏览器标题
  和配置里的 `window_title` 关键字对不上。开了"一条龙"还出现它，说明自动打开/
  切换也没成功，看 `login.log` 里 `---- 开始登录 ----` 之后那几行（会写明是
  找不到 Edge、还是打开了但没等到页面）。
- `fail_no_popup.png`：点了登录但没等到弹窗 —— 看它是没弹、还是被别的窗口挡了。
- `fail_solve_*.png`：面板截下来了但模型没找到缺口 —— 重试机制会自动刷新验证码再试。
- `fail_still_login.png`：滑块过了但页面没跳转 —— 多半是账号/单位不对。

## 目录

```
auto_login_vision/
├── src/
│   ├── main.py       入口：托盘 / 热键 / 调试命令行
│   ├── winapi.py     Win32 底层：截屏 / 鼠标键盘注入 / 剪贴板 / 窗口激活（纯 ctypes）
│   ├── browser.py    一条龙第一步：把登录页弄到前台（复用窗口 / 命令行打开）
│   ├── vision.py     纯视觉定位：登录表单 / 输入框 / 滑块弹窗 / 图片面板
│   ├── solver.py     缺口求解：YOLOv8-seg 为主 + Canny 掩膜模板匹配兜底
│   ├── track.py      拟人拖动轨迹（贝塞尔 + 缓动 + 抖动 + 过冲回拉）
│   ├── flow.py       流程编排与视觉校验
│   ├── hotkey.py     热键文本解析 / 显示格式化 / 真实按键采集
│   ├── tray.py       托盘图标 + 多组全局热键监听
│   ├── gui.py        极简配置界面（tkinter）
│   └── config.py     配置存取
├── captcha_recognizer/  YOLOv8-seg 分割模型（slider.onnx，约 40MB）
├── assets/app.ico    托盘 / exe 图标
├── .github/workflows/build.yml  CI：构建 exe + 打标签自动发 Release
├── requirements.txt  依赖清单（跑源码 / CI 打包都装这一份）
├── _ref/             开发期参考截图（已 gitignore）
├── _debug/           运行期调试图与日志（已 gitignore）
├── config.example.json  配置模板（复制成 config.json 使用）
├── config.json       你本机的真实配置（**已 gitignore，含账号密码**）
├── .gitignore
├── run.bat
└── build.bat
```

> `config.json`、`_debug/`、`_ref/`、`dist/`、`build/` 都在 `.gitignore` 里 ——
> 真实账号、站点截图和调试日志不会被提交。
