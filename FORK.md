# Fork 维护模型 (zggit/karrio)

> 2026-06-12 确立：以我们为主，官方更新时按需同步。

## 分支结构

| 分支 | 角色 | 规则 |
|---|---|---|
| **`production`**（默认分支） | 我们的生产线：官方代码 + 自有补丁（平台同步 / 品牌化 / USPS 合同价 rateIndicator）。生产 Karrio 镜像从它构建（服务器 `/root/karrio-fork`） | 日常开发与提交都在这里 |
| `main` | 官方上游镜像，**不直接开发** | 仅同步时更新 |
| upstream 远端 | `https://github.com/karrio/karrio.git`（已配置） | 只读 |

## 同步官方更新（官方发版时执行）

```bash
git fetch upstream
git checkout main && git merge --ff-only upstream/main && git push origin main
git checkout production && git merge main          # 冲突面≈我们补丁涉及的文件
python -m unittest discover -v -f modules/connectors/usps/tests   # 合同价补丁回归
git push origin production
# 然后重建生产 Karrio 镜像(服务器 /root/karrio-fork git pull 后按现行流程构建)
```

## 历史备注

`production` 的前身是 `branding/app-name-generic`（最初只为 APP_NAME 品牌化而建，
后演变为事实生产分支，2026-06-12 更名归位）。
