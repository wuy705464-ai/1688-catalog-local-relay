# 1688 外贸目录：油猴 + 本机中转 + 豆包选图

这是面向海外客户目录的本机工作流。人工打开已获授权访问的 1688 商品页，油猴一次性采集该商品的文字、分类、1688 单价、规格和候选图片 URL；本机服务按 `offer_id` 写入 SQLite；后台为每个商品下载 8 张候选图，让豆包视觉模型选出 3 张代表图；最后导出带图片的 Excel 客户目录。

详细部署和故障排查见 [LOCAL_RELAY.md](LOCAL_RELAY.md)。

## 数据流

```text
1688 商品页
  -> Tampermonkey IndexedDB 待发送队列
  -> http://127.0.0.1:8765
  -> data/catalog.db
  -> 后台下载 8 张候选图
  -> 豆包视觉模型选 3 张
  -> products/selected/<offer_id>/<record_hash>/01..03.jpg
  -> Excel + manifest.json
```

浏览器队列只做断网缓冲；服务收到后立即落盘并从浏览器删除已同步记录，所以 1,000 个商品不会长期占用油猴存储。

## 关键保证

- 商品主键为从详情 URL 校验出的纯数字 `offer_id`。
- 标题、分类、价格、尺寸、规格和图片 URL 作为同一个原子记录提交。
- 每次内容变化都会生成新的 `record_hash`；旧版本 AI 结果不能回写新版本。
- SQLite、图片目录和 Excel 导出都同时校验 `offer_id + record_hash`。
- 每个完成商品在硬盘上只保留 3 张入选原图；8 张候选图处理完成后删除。
- Excel 导出前强制检查 3 张图的外键、版本目录、排序和文件存在性，任一冲突就停止该商品，而不是猜测配对。
- 每份 Excel 同时生成 manifest，记录行号、`offer_id`、版本哈希和 3 张图片哈希，方便抽查。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

复制 `.env.example` 到项目外的私密位置并填写：

```dotenv
ARK_API_KEY=...
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3/chat/completions
ARK_MODEL=doubao-seed-2-0-lite-260215
```

`ARK_MODEL` 必须是 Model ID 或 `ep-*` 推理接入点 ID，不能填写控制台中以 `api-key-*` 开头的 API Key 名称。实际密钥文件不要复制到本项目，也不要提交到 Git。

## 启动本机中转

先在 `config.yaml` 修改本机令牌：

```yaml
relay:
  token: "换成一段随机长字符串"
```

然后启动：

```powershell
python -m src.relay_api --env-file "D:\private\doubao.env"
```

健康检查：`http://127.0.0.1:8765/health`。服务被强制限制为 localhost，不应开放到局域网或公网。

## 安装油猴脚本

1. 在 Tampermonkey 新建脚本，粘贴 `userscripts/1688-catalog-local-relay.user.js`。
2. 打开任意真实 1688 商品详情页。
3. 在右下角面板填写与 `config.yaml` 相同的 Relay Token。
4. 点击“采集当前商品”。断网时记录会留在 IndexedDB，服务恢复后自动重发；也可点击“立即同步”。
5. 面板支持导出待发送队列 JSON，作为浏览器侧紧急备份。

## 导出海外客户 Excel

```powershell
python scripts\export_customer_catalog.py
```

默认按分类分别导出，每个文件最多 150 个商品。客户表中的主要字段为：

- B：分类
- C：稳定 SKU（`1688-<offer_id>`）
- D：商品标题
- E：AI 入选 3 图横向拼图
- F：规格摘要
- G：1688 原始单价
- K：材质（规格中存在时）
- L：尺寸

数据库、下载图片和导出结果默认分别位于 `data/`、`products/selected/` 和 `output/`，均已加入 `.gitignore`。

## 测试

```powershell
python -m unittest tests.test_local_relay tests.test_image_worker tests.test_catalog_export tests.test_relay_api -v
node --check userscripts\1688-catalog-local-relay.user.js
```

真实豆包测试会产生一次小额调用：

```powershell
python tests\real_doubao_selection.py --env-file "D:\private\doubao.env"
```

## 安全与边界

- 只采集你有权访问和使用的数据；遇到验证码、访问限制或信息冲突时停止该商品并人工核查。
- 项目不需要保存 1688 登录 Cookie。旧 Cookie 文件和旧配置已从当前版本移除。
- `.env`、`*.env`、数据库、日志、下载图和输出目录不会进入 Git。
- `QUICKSTART.md`、`NEXT_STEPS.md` 及旧 `src/pipeline.py` 是历史 Playwright 路线，仅供参考；当前入口是 `src.relay_api`。
