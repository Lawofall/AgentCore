import { Badge, Button } from "@/components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * 「在哪工作」说明弹窗——对着菜单两问：这次聊哪、本机目录怎么用。
 *
 * 文案面向普通用户：入口名与「在哪工作」菜单逐字一致，内部实现词与设计文档术语一律不出现
 * （同名测试守着，防抄设计文档回潮）。
 * 桌面第一屏并列「本地对话 / 云端对话」（默认本地）；网页/手机只有云端对话。
 */
export function WorkspaceChannelGuideDialog({
  open,
  onOpenChange,
  showLocalTraditional,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 有本机盘（桌面端）才讲本机目录三选；Web 只讲云。 */
  showLocalTraditional: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>在哪工作：怎么选</DialogTitle>
          <DialogDescription>
            先选这次聊哪。电脑上的文件夹要另选怎么用。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 px-5 pb-2 text-sm text-foreground">
          <section className="space-y-2 rounded-lg border border-border bg-muted/20 p-3">
            <div className="flex items-center gap-1.5">
              <h3 className="text-sm font-medium text-foreground">这次聊哪</h3>
              {showLocalTraditional ? null : <Badge tone="primary">推荐</Badge>}
            </div>
            {showLocalTraditional ? (
              <p className="text-xs leading-relaxed text-muted-foreground">
                两个都能直接聊，不用先选文件夹。默认在这台电脑上写文件、跑代码。对话记录仍在云上，不是离线。想换手机或网页接着改同一份文件，选云端对话。
              </p>
            ) : (
              <p className="text-xs leading-relaxed text-muted-foreground">
                你在电脑、手机、网页看到的是同一份。它不会自动同步到你电脑：想在自己电脑上拿到，手动导出到某个文件夹，或者导出
                ZIP。
              </p>
            )}
            <dl className="space-y-2">
              {showLocalTraditional ? (
                <div className="space-y-0.5">
                  <div className="flex items-center gap-1.5">
                    <dt className="text-xs font-medium text-foreground">
                      本地对话
                    </dt>
                    <Badge tone="primary">推荐</Badge>
                  </div>
                  <dd className="text-xs leading-relaxed text-muted-foreground">
                    不用先选地方，想到什么直接聊；文件和运行都在这台电脑
                  </dd>
                </div>
              ) : null}
              <div className="space-y-0.5">
                <dt className="text-xs font-medium text-foreground">
                  云端对话
                </dt>
                <dd className="text-xs leading-relaxed text-muted-foreground">
                  {showLocalTraditional
                    ? "不用先选地方，想到什么直接聊；文件和运行在云上，手机和网页也能接着改"
                    : "不用先选地方，想到什么直接聊；真要存文件时会自动建一个文件夹"}
                </dd>
              </div>
              <div className="space-y-0.5">
                <dt className="text-xs font-medium text-foreground">
                  已有文件夹
                </dt>
                <dd className="text-xs leading-relaxed text-muted-foreground">
                  {showLocalTraditional
                    ? "点云图标的接着聊；点硬盘图标的，会再问怎么用"
                    : "点列表里的文件夹接着聊"}
                </dd>
              </div>
              <div className="space-y-0.5">
                <dt className="text-xs font-medium text-foreground">
                  {showLocalTraditional ? "新建或加入…" : "新建文件夹"}
                </dt>
                <dd className="text-xs leading-relaxed text-muted-foreground">
                  {showLocalTraditional
                    ? "新建一个空文件夹，或从 Git 克隆一份到云上；电脑上已有的目录走「从本机加入」"
                    : "建一个空文件夹，从头开始"}
                </dd>
              </div>
            </dl>
          </section>

          {showLocalTraditional ? (
            <section className="space-y-2 rounded-lg border border-border/60 p-3">
              <h3 className="text-sm font-medium text-foreground">
                电脑上的文件夹，选完再选怎么用
              </h3>
              <p className="text-xs leading-relaxed text-muted-foreground">
                点列表里硬盘图标那一行，或走「从本机加入」选一个新目录，都会再问怎么用。不是离线模式：模型调用一样要联网，对话记录也仍然存在云上。
              </p>
              <dl className="space-y-2">
                <div className="space-y-0.5">
                  <dt className="text-xs font-medium text-foreground">
                    直接改这个文件夹
                  </dt>
                  <dd className="text-xs leading-relaxed text-muted-foreground">
                    改的就是你电脑上的那个目录，不用先复制上来，适合东西本来就在电脑上、或者非得用你电脑上那套环境的活
                  </dd>
                </div>
                <div className="space-y-0.5">
                  <dt className="text-xs font-medium text-foreground">
                    复制到云上当新家
                  </dt>
                  <dd className="text-xs leading-relaxed text-muted-foreground">
                    把你电脑上的文件夹复制一份上来；之后改的是云上这份，电脑里的原件不会跟着变
                  </dd>
                </div>
                <div className="space-y-0.5">
                  <dt className="text-xs font-medium text-foreground">
                    先在云上做，原件先不动
                  </dt>
                  <dd className="text-xs leading-relaxed text-muted-foreground">
                    这一单在云上做，电脑里的原件先不动；做完再决定写不写回。不是复制上来当云上那份家。
                  </dd>
                </div>
              </dl>
            </section>
          ) : null}

          <section className="space-y-1.5 px-0.5">
            <h3 className="text-sm font-medium text-foreground">怎么选</h3>
            <ul className="space-y-1 text-xs leading-relaxed text-muted-foreground">
              <li>
                {showLocalTraditional
                  ? "日常在这台电脑写、跑 → 本地对话"
                  : "日常用、想在手机和网页接着看 → 云端对话、已有云文件夹或新建文件夹"}
              </li>
              {showLocalTraditional ? (
                <li>
                  要手机和网页接着改同一份 →
                  云端对话、已有云文件夹，或新建或加入
                </li>
              ) : null}
              {showLocalTraditional ? (
                <li>
                  东西已经在你电脑上、又要用你电脑上的环境 → 直接改这个文件夹
                </li>
              ) : null}
              {showLocalTraditional ? (
                <li>
                  电脑上的文件夹也想换设备接着用 →
                  复制到云上当新家（可选，不必搬）
                </li>
              ) : null}
              {showLocalTraditional ? (
                <li>
                  这一单想在云上做、电脑上的原件先不动 → 先在云上做，原件先不动
                </li>
              ) : null}
            </ul>
          </section>
        </div>

        <DialogFooter className="gap-3 sm:items-center sm:justify-between">
          <p className="text-xs text-muted-foreground sm:max-w-[18rem]">
            这次选的会记住，下次默认还从这里开始。
          </p>
          <Button type="button" onClick={() => onOpenChange(false)}>
            知道了
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
