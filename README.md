# 涨潮断面通行簿

内河调度员用潮尺、站点基准和船期表核对放行时间。工作台默认打开断测场次，左侧可依次查看采信、测站归一、阈值交点、全程潮窗、船期覆盖、运行留档和证据签发。

## 启动

```sh
docker compose up --build
```

访问 `http://localhost:18472`。本机也可安装 `requirements.txt` 后执行 `python -m app.main`。留档存入 `TIDE_DB` 指定的 SQLite 库，默认 `var/tide.sqlite3`。清空演示归档时先停止应用，再移除该库或 Compose 命名卷。

## 复核

1. 首页选海棠17，核对 3000 秒窗口中的 1500 秒可行、900 秒不可行、600 秒缺证。
2. 打开全程可行潮窗，查看放行起止、临界点和断面潮尺行。
3. 打开运行留档，复算并保留原归档号，再到潮窗证据签发选船下载本船凭据 JSON，或下载港方整批留底、打印验签。
4. 切换连续测站样例，海棠17的全部 3000 秒均可行。

`GET /api/stage/{readings,datum,crossings,windows,coverage}` 可查看样例阶段结果。对同路径执行 POST 可提交 `{"sources":{"observations":"...","stations":"...","vessels":"..."}}`。`POST /api/journal` 接受同一输入或 `{"sample":"dirty"}`。`GET /api/journal/<id>` 查归档，`POST /api/journal/<id>/rerun` 复算，`GET /api/export/<id>` 下载港方整批留底。

## 按船签发与离线验真

船方凭据按船下载，不随证附上其他船的吃水、申请时刻或整批结论：

- `GET /api/export/<id>/vessel/<船名>`：船方凭据。只含本船船期行、本船的交点/窗口/覆盖结论，以及共享潮尺与站点原件；别船资料仅以全簿指纹锚定，不离开港方留档。
- `GET /api/export/<id>`：港方留底，保留整批原件与全船结论，不发给船方。
- `POST /api/export/verify`（可带 `?recompute=1`）：验真入口。船方离线可用 `python -m app.verify 凭据.json [--recompute]` 自行核验。

凭据含两道签名：内容封缄（SHA-256，检出内容改动）与港方 HMAC 港签（证明本港签发，密钥取环境变量 `TIDE_SECRET`）。验真先验港签，再核留档锚点，`--recompute` 时用随证共享潮尺、站点原件和本船船期行重算本船结论。重试签发沿用同一留档号且凭据字节确定；调包潮尺或船期后，港签、锚点或复算任一环节即验不过。未配置 `TIDE_SECRET` 时退回与留档库绑定的派生密钥，仅限本机演示，部署必须显式注入密钥。

字段见 [数据字典](docs/DATA-DICTIONARY.md)，规则与回归见 [深度登记](docs/DEPTH.md)。本机运行 `python -m pytest -q`。
