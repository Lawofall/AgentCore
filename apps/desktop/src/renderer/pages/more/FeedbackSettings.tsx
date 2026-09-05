import {
  SettingField,
  SettingsAsync,
  SettingsFormMessage,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import {
  Button,
  Card,
  Input,
  PageHeader,
  Select,
  Textarea,
} from "@/components/ui";
import { errMsg } from "@/lib/errMsg";
import { api } from "@/services/api";
import { Loader2 } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";

interface FeedbackSummary {
  id: string;
  category: string;
  title: string;
  description: string;
  page_context: string | null;
  status: string;
  admin_reply: string | null;
  created_at: string;
  updated_at: string;
}

interface FeedbackListResponse {
  data: FeedbackSummary[];
  total: number;
}

const CATEGORY_OPTIONS = [
  { value: "bug", label: "Bug报告" },
  { value: "feature", label: "功能需求" },
  { value: "improvement", label: "体验改进" },
  { value: "other", label: "其他" },
] as const;

function categoryBadgeClass(category: string): string {
  switch (category) {
    case "bug":
      return "text-destructive bg-destructive/10";
    case "feature":
      return "text-accent-foreground bg-accent";
    case "improvement":
      return "text-success bg-success/10";
    default:
      return "text-muted-foreground bg-muted";
  }
}

function categoryLabel(category: string): string {
  return CATEGORY_OPTIONS.find((o) => o.value === category)?.label ?? category;
}

function statusLabel(status: string): string {
  switch (status) {
    case "open":
      return "待处理";
    case "acknowledged":
      return "已确认";
    case "resolved":
      return "已解决";
    case "closed":
      return "已关闭";
    default:
      return status;
  }
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function FeedbackSettings() {
  const [category, setCategory] = useState<string>("bug");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState(false);

  const [feedbackList, setFeedbackList] = useState<FeedbackSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const loadFeedback = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const res = await api.get<FeedbackListResponse>("/v1/feedback");
      setFeedbackList(res.data);
    } catch (e) {
      setListError(errMsg(e, "加载历史反馈失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadFeedback();
  }, [loadFeedback]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmedTitle = title.trim();
    const trimmedDescription = description.trim();
    if (!trimmedTitle || !trimmedDescription) {
      setSubmitError("请填写标题和描述");
      return;
    }

    setSubmitting(true);
    setSubmitError(null);
    setSubmitSuccess(false);
    try {
      await api.post<FeedbackSummary>("/v1/feedback", {
        category,
        title: trimmedTitle,
        description: trimmedDescription,
        page_context: window.location.hash || null,
      });
      setCategory("bug");
      setTitle("");
      setDescription("");
      setSubmitSuccess(true);
      await loadFeedback();
    } catch (err) {
      setSubmitError(errMsg(err, "提交失败，请重试"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <PageHeader title="反馈" />

      <SettingsStack>
        <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
          <SettingField label="分类" htmlFor="feedback-category">
            <Select
              id="feedback-category"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            >
              {CATEGORY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </SettingField>

          <SettingField label="标题" htmlFor="feedback-title">
            <Input
              id="feedback-title"
              value={title}
              maxLength={200}
              placeholder="简要描述问题或建议"
              onChange={(e) => setTitle(e.target.value)}
            />
          </SettingField>

          <SettingField label="详细描述" htmlFor="feedback-description">
            <Textarea
              id="feedback-description"
              rows={4}
              value={description}
              maxLength={5000}
              placeholder="请尽量详细说明，便于我们定位和跟进"
              onChange={(e) => setDescription(e.target.value)}
            />
          </SettingField>

          <SettingsFormMessage tone={submitError ? "error" : "success"}>
            {submitError ?? (submitSuccess ? "提交成功" : null)}
          </SettingsFormMessage>

          <div className="flex justify-end">
            <Button
              type="submit"
              size="md"
              disabled={submitting}
              icon={
                submitting ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : undefined
              }
            >
              提交反馈
            </Button>
          </div>
        </form>

        <SettingsSection title="历史反馈" divider>
          <SettingsAsync
            loading={loading}
            error={listError}
            empty={feedbackList.length === 0}
            emptyLabel="暂无反馈记录"
            onRetry={() => void loadFeedback()}
          >
            <ul className="space-y-3">
              {feedbackList.map((item) => (
                <li key={item.id}>
                  <Card className="p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded-lg px-2 py-0.5 text-xs font-medium ${categoryBadgeClass(item.category)}`}
                      >
                        {categoryLabel(item.category)}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {statusLabel(item.status)}
                      </span>
                      <span className="ml-auto text-xs text-muted-foreground">
                        {formatDate(item.created_at)}
                      </span>
                    </div>
                    <p className="mt-2 text-sm font-medium text-foreground">
                      {item.title}
                    </p>
                    {item.admin_reply && (
                      <p className="mt-2 text-xs text-muted-foreground">
                        <span className="font-medium text-foreground">
                          回复：
                        </span>
                        {item.admin_reply}
                      </p>
                    )}
                  </Card>
                </li>
              ))}
            </ul>
          </SettingsAsync>
        </SettingsSection>
      </SettingsStack>
    </div>
  );
}
