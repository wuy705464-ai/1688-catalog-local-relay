# 1688 本机中转版 v3

这是当前推荐流程：人工打开真实 1688 商品页，油猴负责一次性采集同一商品的文字、价格、规格和候选图片 URL；本机服务立即按 `offer_id` 写入 SQLite，后台再下载最多 8 张候选图并调用豆包视觉模型选出 3 张。

## 为什么不会把 A 商品图片写到 B 商品

系统使用三重约束：

1. API 会校验当前网址里的 offer id 必须等于请求里的 `offer_id`，不一致直接拒绝。
2. SQLite 的商品、图片、后台任务都用同一个 `offer_id` 外键；每次重新采集还会生成 `record_hash`。
3. 后台选图完成时必须再次匹配 `offer_id + record_hash`。采集期间如果商品记录被更新，旧任务结果会被丢弃，不会写入新记录。

最终图片目录如下，每个版本只保留 3 张：

```text
products/selected/<offer_id>/<record_hash前16位>/
  01.jpg
  02.jpg
  03.jpg
```

导出程序还会校验图片路径、外键、版本哈希和 1–3 排名。任何一项不一致都会停止导出，不会“猜着配”。

## 1. 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. 配置本机令牌

修改 `config.yaml`：

```yaml
relay:
  token: "换成你自己的随机长字符串"
```

Tampermonkey 安装 `userscripts/1688-catalog-local-relay.user.js` 后，在页面右上角面板点击“设置令牌”，填入相同内容。

## 3. 启动服务并加载豆包环境变量

真实密钥只从外部 `.env` 文件读入，不要复制到项目目录：

```powershell
python -m src.relay_api --env-file "C:\path\to\1688-doubao.env"
```

浏览器访问 `http://127.0.0.1:8765/health`，看到 `{"ok":true,...}` 即为正常。服务只允许绑定 localhost。

需要的变量名：

```text
ARK_API_KEY
ARK_BASE_URL
ARK_MODEL
```

`ARK_MODEL` 必须填写可调用的 Model ID（例如配置文件中的默认视觉模型）或 `ep-*` 推理接入点 ID；控制台里以 `api-key-*` 开头的 API Key 名称不是模型。若误填这种名称，服务会明确告警并使用 `config.yaml` 的 `vision.model` 作为备用值。

## 4. 采集

1. 登录 1688，人工打开商品详情页。
2. 油猴等待页面稳定后采集一个原子商品记录。
3. 记录先进入浏览器 IndexedDB 待发送队列，再同步到本机 SQLite。
4. 服务离线时记录不会丢；服务恢复后面板会自动重试。
5. 后台任务下载 8 张有效候选图，豆包选 3 张，未选中的临时文件会删除，但原始 URL 仍在 SQLite，可以重新处理。

SQLite 默认位置：`data/catalog.db`。

## 5. 导出海外客户目录

```powershell
python scripts\export_customer_catalog.py
```

默认按分类拆分，每份最多 150 件。三张图会在 Excel 中合成一张横向三图预览，硬盘上仍只保留 3 张选中原图。

指定分类：

```powershell
python scripts\export_customer_catalog.py --category "项链"
```

输出目录：`output/customer_catalogs/`。每个 Excel 会同时生成本机核对用的 `.manifest.json`，记录 Excel 行号、offer id、record hash 和三张图片哈希。

## 故障处理

- `401 invalid relay token`：油猴面板令牌与 `config.yaml` 不一致。
- `only N valid candidate images`：主图区域没有拿到至少 3 张清晰图片，记录会重试，绝不拼入其他商品图片。
- 豆包调用失败：`require_ai: true` 时任务会重试并最终标记失败，不会悄悄改用规则选图。
- 页面重新采集：相同数据幂等；文字或图片 URL 发生变化时会创建新版本选图任务。

## 安全

- `.env`、数据库、下载图片和输出目录均在 `.gitignore` 中。
- 服务仅监听 `127.0.0.1`。
- 下载器只允许 `alicdn.com` 和 `1688.com` 图片域名，并限制单图大小与重定向。
