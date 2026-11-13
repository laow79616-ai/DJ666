# DJ666

DJ666 是一个音乐社区站点：用户可注册登录、播放与上传音乐、浏览公告、参与公屏聊天。管理员可审核曲目、发布公告、进行音频优化、从 Telegram 频道导入音频，并查看自动备份记录。

本仓库同步的是服务器上正在运行的应用代码（不含用户数据、上传文件与密钥）。

## 技术栈

- Python / FastAPI + Jinja2 + SQLite
- Nginx 反代，systemd 托管 uvicorn
- 可选：Telethon（后台 Telegram 音频导入）、rsync + systemd timer（自动备份）

## 目录说明

- `main.py`、`templates/`、`static/`：网站应用
- `scripts/`：自动备份脚本及对应的 systemd/sudoers 示例
- `deploy/`：systemd unit 与 Nginx 站点配置示例（部署时请按本机路径调整）
- `.env.example`：环境变量模板

**不会也不应纳入仓库的内容：** `data/`（数据库 `app.db`、Telegram session 与密钥）、`.env`、`uploads/`、`backups/`、`venv/`、日志和任何 `*.db` / `*.enc` / `*.key`。

## 部署概要

以 Debian/Ubuntu、部署目录 `/opt/dj666` 为例。

1. 安装依赖：`python3`、`python3-venv`、`ffmpeg`、`nginx`、`rsync`。
2. 将代码放到 `/opt/dj666`，创建虚拟环境并安装：

   ```bash
   cd /opt/dj666
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   ```

3. 复制环境变量并修改：

   ```bash
   cp .env.example .env
   chmod 600 .env
   # 至少填写 SECRET_KEY、PUBLIC_BASE_URL
   mkdir -p data uploads
   chown -R www-data:www-data /opt/dj666
   ```

4. 安装 `deploy/dj666.service` 到 `/etc/systemd/system/dj666.service`，`systemctl enable --now dj666.service`。
5. 按 `deploy/nginx-dj666.pw.conf`（或较简的 `nginx-http.example.conf`）配置 Nginx，并用 Certbot 申请证书。生产环境请保留较大的 `client_max_body_size`（当前站点为 4096m）。
6. 可选自动备份：安装 `deploy/dj666-backup.service`、`dj666-backup.timer` 以及 `dj666-backup.sudoers`（先 `visudo -cf`），再 `systemctl enable --now dj666-backup.timer`。备份默认写入 `/opt/dj666_backups/auto/`，每 10 分钟一次；上传音频用硬链接增量快照，不包含 `uploads/optimize`。

首次启动且数据库中还没有用户时，会创建一个管理员账号（邮箱 `admin@dj666.pw`，默认密码见 `main.py` 中的初始化逻辑）。**请立刻在后台修改密码。** Bot Token、Telegram `api_id` / `api_hash` 请只在后台配置，不要写入仓库。

## 许可与内容

请仅上传或导入自己拥有版权或已获授权的音频。
