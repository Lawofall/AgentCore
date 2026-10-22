/**
 * 草稿页空态：单句问候。平台代付、开箱即用——无「先接入模型」门。
 * 仅在 ChatView 无消息时渲染。不讲产品心智、不链手册、不放任务 chips。
 */
export function DraftEmptyState() {
  return (
    <div className="text-center" data-empty-kind="greeting">
      <p className="text-2xl font-medium text-foreground">
        今天想解决什么问题？
      </p>
    </div>
  );
}
