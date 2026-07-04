---
name: prod-ops
description: 生产服务器(root@45.77.203.235)日常运维查询手册:karrio DB schema 速查、bench console 调用铁律、eBay/Tongtool API 入口、每日查单与对账流程。凡 SSH 生产做查询/诊断,先读本技能,不要现场考古。
---

# 生产运维手册(prod-ops)

主机 `root@45.77.203.235`,双栈:ERP(erp-backend-1 等 erp-* 容器)+ Karrio(karrio.api/worker/db/dashboard)。
主机与容器时区均 America/New_York;Postgres 存储 UTC。部署/补丁手法见 karrio-gotchas 技能,本技能只管**只读查询**。

## 1. karrio 数据库(容器 karrio.db,postgres:16)

连接咒语(env 变量在容器内展开,勿在本机展开):
```bash
ssh -o ConnectTimeout=10 root@45.77.203.235 \
  "docker exec -i karrio.db sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'" <<'EOF'
SELECT ...;
EOF
```

**Schema 速查(别再猜)**:
- 表名全是复数:`shipments`、`orders`、`manifests`、`pickups`;连接表 `orders_shipments(order_id, shipment_id)`
- `shipments.recipient` / `shipments.shipper` 是 **jsonb 内嵌列**(不是外键!):`s.recipient->>'person_name'`、`->>'address_line1'`、`->>'city'`、`->>'postal_code'`
- `orders.metadata` jsonb 关键键:`tt_sales_record`(**= eBay orderId**,格式 `01-14858-47149`)、`tt_writeback`(pending|retrying|done|rejected)、`tt_packet_id`、`tt_channel`
- `shipments.selected_rate` jsonb:`->>'carrier_name'`、`->>'service'`
- 面单状态惯例:购买成功后长期停留 `created`,USPS 揽收扫描后才走(正常,勿当异常)
- 承运商连接表:`"CarrierConnection"` / `providers_carrier` 类大小写混合表名要加双引号

**今日打单数(双口径规则:回答"多少单"必须同时给打单数和新订单数,7/3 已发生过歧义)**:
```sql
SELECT count(*) AS labels, count(DISTINCT o.metadata->>'tt_sales_record') AS orders,
       min(to_char(s.created_at AT TIME ZONE 'America/New_York','HH24:MI')) AS first,
       max(to_char(s.created_at AT TIME ZONE 'America/New_York','HH24:MI')) AS last
FROM orders o JOIN orders_shipments os ON os.order_id=o.id JOIN shipments s ON s.id=os.shipment_id
WHERE s.created_at >= date_trunc('day', now() AT TIME ZONE 'America/New_York') AT TIME ZONE 'America/New_York';
```

近 N 天分布:同上 WHERE 改 `- interval '6 days'`,`GROUP BY (s.created_at AT TIME ZONE 'America/New_York')::date`。

## 2. bench console 调用铁律(容器 erp-backend-1)

```bash
ssh -o ConnectTimeout=10 root@45.77.203.235 \
  "docker exec -i erp-backend-1 bench --site 45.77.203.235 console" <<'EOF'
...python...
EOF
```
- **多行块(for/if)会被 IPython 管道吞掉**。三个安全形态:①全部单行语句(分号连接);②循环包进 `exec('...\n...')`;③本机写好脚本文件后 `console < file`
- 输出第一行会带 `In [n]:` 前缀——grep 用 `grep -o "TAG: .*"`(**不要**锚定 `^TAG`,会丢首行)
- ERP2 Tongtool 接口限速 5 次/分钟,物流接口 60 次/分钟——循环里 `time.sleep`

## 3. eBay API(店铺 AV=anyvolume,ST=shoptouchusa)

入口(app 自带,勿裸调):
```python
from ebay_integration.api.ebay_client import get_access_token, get_base_url, fetch_single_order
token = get_access_token("AV")   # 店铺名就是 "AV"/"ST"
url = get_base_url() + "/sell/fulfillment/v1/order"
```
- awaiting shipment 计数:GET 该 url,`params={"filter": "orderfulfillmentstatus:{NOT_STARTED|IN_PROGRESS}", "limit": 1}`,读 `total`。**单值 `{NOT_STARTED}` 会 400**,必须双值集合
- 今日新订单:`filter=creationdate:[YYYY-MM-DDT04:00:00.000Z..]`(美东零点=UTC 04:00,冬令时 05:00)
- 补推 tracking:`from ebay_integration.fulfillment.ebay_outbound import push_tracking; push_tracking(ebay_order_id, "AV", tracking_number)`——幂等(409 已发货=成功),缺 line_items 自动拉。**这是写操作,批量前必须用户点头**

## 4. eBay↔karrio 对账(同步看门狗手动版)

对账键:`orders.metadata->>'tt_sales_record'` **就是 eBay orderId**;**不是**eBay 的数字型 `salesRecordReference`——7/2 曾因此得出零重叠假结论。流程:
1. karrio 侧:今日(或指定窗口)有 tracking 的 `tt_sales_record` 清单(§1 SQL 加 `AND s.tracking_number IS NOT NULL`)
2. eBay 侧:awaiting 列表 orderId(§3,limit=200 单页够)
3. 求交集:交集>0 = 已打面单但 tracking 未到 eBay(Tongtool 标发断档,参考 memory `project-ebay-tracking-sync-gap` 的 7/2 事故)
4. 修复:逐单 `push_tracking`(先试点 2-3 单验证 FULFILLED,再批量;批量需用户授权)

自动化版:服务器上 `/root/karrio/watchdog/karrio-sync-watchdog.sh`(systemd timer 每天 15:30 ET),手动触发 `--dry-run` 只读输出。

## 5. 健康检查电池

```bash
ssh root@45.77.203.235 'docker ps --format "{{.Names}}\t{{.Status}}"'          # 容器清单
ssh root@45.77.203.235 'docker stats --no-stream --format "{{.Name}}\t{{.MemUsage}}"'
ssh root@45.77.203.235 'free -h; journalctl -u karrio-recycle -n 20 --no-pager'
ssh root@45.77.203.235 'journalctl -u karrio-sync-watchdog -n 20 --no-pager'   # 对账看门狗日志
```
gunicorn 内存回收:每天 04:00 ET systemd timer(karrio-recycle),详见 ebay_integration/deploy/karrio/recycle/README.md。

## 6. Tongtool 侧边界

- 授权状态 API 查不到(524),只能网页后台:erp.tongtool.com → 基础设置 → 账号授权管理(详见 tongtool-api-docs 技能"死胡同"节)
- writeback 状态在我们这边:`orders.metadata->>'tt_writeback'`,全 `done` = Tongtool 手里有 tracking,断档必在它的"标发上传"环节
