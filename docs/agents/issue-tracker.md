# GitHub Issues 工单约定

本仓库的规格和工单存放在 `WilliamJoyce1997/pylangchain` 的 GitHub Issues。使用 `gh` 操作。首次写入前，运行 `gh repo view --json nameWithOwner --jq .nameWithOwner` 核对目标。需要明确指定仓库时，使用 `--repo WilliamJoyce1997/pylangchain`。

## 工单操作

- 发布规格或工单时，使用 `gh issue create --title "<标题>" --body-file <正文文件>`。多条实施工单按依赖顺序发布。
- 读取工单时，使用 `gh issue view <编号> --json number,title,body,labels,comments`。列出工单时，使用 `gh issue list --state open --json number,title,body,labels,comments`，按需添加 `--label` 或调整状态筛选。
- 将工单关联为子工单时，向 `gh api --method POST repos/WilliamJoyce1997/pylangchain/issues/<父工单编号>/sub_issues -F sub_issue_id=<子工单数据库ID>` 发送请求。如果仓库无法使用子工单，在子工单正文顶部写上 `Part of #<父工单编号>`。
- 添加阻塞依赖时，使用 `gh api --method POST repos/WilliamJoyce1997/pylangchain/issues/<被阻塞工单编号>/dependencies/blocked_by -F issue_id=<阻塞工单数据库ID>`。用 `gh api repos/WilliamJoyce1997/pylangchain/issues/<编号> --jq .id` 获取数据库 ID。用 `gh api repos/WilliamJoyce1997/pylangchain/issues/<编号> --jq .issue_dependencies_summary.blocked_by` 获取未关闭的阻塞项数量。如果无法使用原生依赖，在正文顶部记录 `Blocked by: #<编号>, #<编号>`。全部阻塞工单关闭后即可开始。
- 根据 [标签映射](triage-labels.md)，用 `gh issue edit <编号> --add-label "<标签>"` 或 `--remove-label "<标签>"` 更新标签。
- 添加评论时，使用 `gh issue comment <编号> --body-file <正文文件>`。关闭工单前先单独发表评论说明原因，再运行 `gh issue close <编号>`。

## Pull requests as a triage surface

**PRs as a request surface: no.**

配置为 `yes` 时，对外部 PR 使用同一套分诊标签。用 `gh pr view`、`gh pr diff` 和评论记录了解背景，用 `gh pr comment`、`gh pr edit` 和 `gh pr close` 处理。GitHub Issues 和 PR 共享编号，收到单独的编号时可先用 `gh pr view <编号>` 确认类型。

## Wayfinder

- 用带 `wayfinder:map` 标签的工单作为地图，其正文记录 Notes、Decisions-so-far 和 Fog。
- 将每个任务作为子工单关联到地图，并添加 `wayfinder:<类型>` 标签。如果无法使用子工单，在地图正文添加子工单清单，同时在子工单正文写上 `Part of #<地图编号>`。
- 按地图顺序选择首个仍开放、没有未解决阻塞且未分配的子工单。开始前执行 `gh issue edit <编号> --add-assignee @me`。
- 完成时先写评论、关闭子工单，再把结论摘要和链接追加到地图的 Decisions-so-far。
