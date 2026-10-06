import { newId, nowIso } from "./ids";
import { type ColumnDef, type TableDoc, emptyViewConfig } from "./types";

export const DEMO_TABLE_ID = "demo-table";

export function statusColumn(): ColumnDef {
  return {
    id: newId(),
    label: "状态",
    type: "singleSelect",
    options: [
      { id: newId(), label: "待办", tone: "gray" },
      { id: newId(), label: "进行中", tone: "blue" },
      { id: newId(), label: "完成", tone: "green" },
    ],
  };
}

export function blankTable(title = "未命名表格"): TableDoc {
  const titleCol: ColumnDef = { id: newId(), label: "标题", type: "text" };
  const status = statusColumn();
  const dateCol: ColumnDef = { id: newId(), label: "日期", type: "date" };
  const viewId = newId();
  const now = nowIso();
  const todo = status.options?.[0]?.id ?? null;
  return {
    id: newId(),
    title,
    columns: [titleCol, status, dateCol],
    rows: [
      {
        id: newId(),
        position: 1000,
        cells: { [titleCol.id]: "", [status.id]: todo, [dateCol.id]: null },
      },
    ],
    views: [
      {
        id: viewId,
        name: "表格",
        displayMode: "table",
        config: emptyViewConfig(),
        isDefault: true,
      },
    ],
    activeViewId: viewId,
    schemaVersion: 1,
    conversationId: null,
    createdAt: now,
    updatedAt: now,
  };
}

export function createDemoTable(): TableDoc {
  const titleCol: ColumnDef = { id: "c-title", label: "标题", type: "text" };
  const status: ColumnDef = {
    id: "c-status",
    label: "状态",
    type: "singleSelect",
    options: [
      { id: "s-todo", label: "待办", tone: "gray" },
      { id: "s-doing", label: "进行中", tone: "blue" },
      { id: "s-done", label: "完成", tone: "green" },
    ],
  };
  const dateCol: ColumnDef = { id: "c-date", label: "日期", type: "date" };
  const score: ColumnDef = { id: "c-score", label: "评分", type: "number" };
  const read: ColumnDef = { id: "c-read", label: "已读", type: "checkbox" };
  const link: ColumnDef = { id: "c-link", label: "链接", type: "url" };
  const tags: ColumnDef = {
    id: "c-tags",
    label: "标签",
    type: "multiSelect",
    options: [
      { id: "t-design", label: "设计", tone: "orange" },
      { id: "t-eng", label: "开发", tone: "blue" },
      { id: "t-doc", label: "文档", tone: "gray" },
    ],
  };
  const tableView = {
    id: "v-table",
    name: "表格",
    displayMode: "table" as const,
    config: emptyViewConfig(),
    isDefault: true,
  };
  const kanbanView = {
    id: "v-kanban",
    name: "看板",
    displayMode: "kanban" as const,
    config: {
      ...emptyViewConfig(),
      modeConfig: { groupField: "c-status", titleField: "c-title" },
    },
  };
  const calView = {
    id: "v-cal",
    name: "日历",
    displayMode: "calendar" as const,
    config: {
      ...emptyViewConfig(),
      modeConfig: { dateField: "c-date", titleField: "c-title" },
    },
  };
  const galView = {
    id: "v-gal",
    name: "画廊",
    displayMode: "gallery" as const,
    config: {
      ...emptyViewConfig(),
      modeConfig: {
        titleField: "c-title",
        subtitleFields: ["c-status", "c-date"],
      },
    },
  };
  const now = nowIso();
  // Calendar opens on the current month. Keep demo dates inside it.
  const demoDate = (day: number) => {
    const today = new Date();
    const month = `${today.getMonth() + 1}`.padStart(2, "0");
    return `${today.getFullYear()}-${month}-${`${day}`.padStart(2, "0")}`;
  };
  const rows = [
    {
      id: "r1",
      position: 1000,
      cells: {
        "c-title": "飞书多维表格怎么做筛选",
        "c-status": "s-done",
        "c-date": demoDate(10),
        "c-score": 4,
        "c-read": true,
        "c-link": "https://www.feishu.cn",
        "c-tags": ["t-doc"],
      },
    },
    {
      id: "r2",
      position: 2000,
      cells: {
        "c-title": "看板拖拽改状态",
        "c-status": "s-doing",
        "c-date": demoDate(13),
        "c-score": 5,
        "c-read": false,
        "c-link": "",
        "c-tags": ["t-design", "t-eng"],
      },
    },
    {
      id: "r3",
      position: 3000,
      cells: {
        "c-title": "日历视图按截止日期铺开",
        "c-status": "s-todo",
        "c-date": demoDate(20),
        "c-score": 3,
        "c-read": false,
        "c-link": "",
        "c-tags": ["t-design"],
      },
    },
    {
      id: "r4",
      position: 4000,
      cells: {
        "c-title": "Agent 批量填表",
        "c-status": "s-todo",
        "c-date": demoDate(28),
        "c-score": null,
        "c-read": false,
        "c-link": "",
        "c-tags": ["t-eng"],
      },
    },
    {
      id: "r5",
      position: 5000,
      cells: {
        "c-title": "画廊卡片扫一眼",
        "c-status": "s-doing",
        "c-date": demoDate(16),
        "c-score": 4,
        "c-read": true,
        "c-link": "https://www.notion.so",
        "c-tags": ["t-design"],
      },
    },
  ];
  return {
    id: DEMO_TABLE_ID,
    title: "阅读清单",
    columns: [titleCol, status, dateCol, score, read, link, tags],
    rows,
    views: [tableView, kanbanView, calView, galView],
    activeViewId: "v-table",
    schemaVersion: 1,
    conversationId: null,
    createdAt: now,
    updatedAt: now,
  };
}
