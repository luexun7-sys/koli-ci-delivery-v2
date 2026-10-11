# KOLI CI V2 — 两台手机测试及上线操作指南

> 目前已准备好**可部署的源码**，尚未发布到公网。不要将本地测试结果当成已上线。

## A. 本地检查（电脑）

在项目文件夹运行：

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

电脑浏览器访问：
- 顾客端 `http://127.0.0.1:8000/client`
- 骑手端 `http://127.0.0.1:8000/rider`
- 管理端 `http://127.0.0.1:8000/admin`

测试账号（**只能用于私人本地测试，严禁公网使用**）：
- admin / admin12345
- rider / rider12345

## B. 发布到 Railway（需要你自己的 Railway/GitHub 账号）

1. 将解压后 `koli-ci-delivery` **文件夹里的内容**（包括根目录的 `Dockerfile`）上传到你自己的**私有** GitHub 仓库，不要上传 `.env` 或数据库 `.db`。
2. 在 Railway 新建项目，选择 Deploy from GitHub repo → 这个仓库。自动根据 `railway.json` 和 `Dockerfile` 构建。
3. 在 Railway 中创建**持久化 Volume**，挂载到 `/data`。非常重要：否则容器重启可能丢失 SQLite 订单数据。Docker 启动脚本负责修复卷目录的写入权限，再切换为非管理员用户运行应用。
4. 在服务环境变量中添加：
   - `KOLI_MODE=production`
   - `KOLI_DB=/data/koli.db`
   - `KOLI_ADMIN_PASSWORD=...`（不同于测试密码，至少 14 位）
   - `KOLI_RIDER_PASSWORD=...`（不同于后台密码，至少 14 位）
5. 部署后在 Railway Networking 生成 HTTPS 公网域名。例如如果生成 `https://YOURDOMAIN.example`，那么：
   - 顾客端：`https://YOURDOMAIN.example/client`
   - 骑手端：`https://YOURDOMAIN.example/rider`
   - 后台：`https://YOURDOMAIN.example/admin`
6. 先用测试人员模拟完整配送，不要提前向真实顾客宣传网址。Railway 服务和持久存储可能收费，请在其平台确认实时价格。

**注意：** 环境变量密码仅在数据库里对应账号**首次创建时**生效。线上数据库已有旧用户时，修改环境变量**不会**自动重置账户密码。不要复制本地演示数据库上云。

## C. 两台手机验收

- 手机 A（骑手）：打开 HTTPS `/rider`，登录，授权 GPS，保持页面在前台，打开“在线”。
- 手机 B（顾客）：打开 HTTPS `/client`，先点取货地址按钮并在地图点选，再点送达地址按钮选点，填写测试名字和号码，下单。
- 骑手刷新任务列表（或等待约 5.5 秒），应看到派单；逐一更新“前往取件、已取件、配送中、已送达”。
- 顾客在“Suivi”查看每一步状态变化及骑手新近 GPS。可复制私密订单追踪链接给另一台手机。
- 骑手在配送中不得后台锁屏：iOS 浏览器不会保证持续后台 GPS。GPS 超过约 120 秒未更新，系统不会将其用于自动分配**新**任务；已派单任务状态仍正常存在。
- 配送完结后再次查看顾客端：不得看见骑手后续的 GPS 坐标。

## D. 安装到手机桌面

- iPhone：Safari 打开 HTTPS `/client` 或 `/rider` → 分享 → 添加到主屏幕。
- Android：Chrome 打开对应链接 → 菜单 → 添加到主屏幕/安装应用（视浏览器而异）。
- 安装方式不等于原生 App，后台 GPS 仍受系统限制。

## E. 正式运营仍需开发（不可跳过）

公网暴露前至少需要：登录/下单限流、防骚扰验证、用户隐私政策及 GPS 保存期限、数据库备份与迁移、地图服务商的生产授权、支付合规、短信/WhatsApp 通知、骑手实名审核、订单取消及异常处理、应用和服务监控。当前使用 OpenStreetMap、公开 Nominatim 和 OSRM 演示接口，不适合直接大规模商用。Web MVP 不能保证骑手手机锁屏之后的实时 GPS。

