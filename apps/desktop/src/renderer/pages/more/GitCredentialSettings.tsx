import {
  SettingField,
  SettingsAsync,
  SettingsFormMessage,
  SettingsStack,
} from "@/components/settings";
import { Button, Card, ConfirmDialog, Input } from "@/components/ui";
import { errMsg } from "@/lib/errMsg";
import { gitCredentialKeys } from "@/lib/queryKeys";
import {
  deleteGitCredentials,
  getGitCredentials,
  upsertGitCredentials,
} from "@/services/gitCredentials";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { SettingsHeader } from "./SettingsHeader";

/**
 * Git 凭据 (/more/git) — 本页只存账户 Token（云私仓）。
 * HTTP 用户名由服务端固定为 GitHub PAT 默认值，不进公开契约。
 * 本地仓继承 OS / `gh auth`，不在本页说明；克隆入口在文件页。
 */
export function GitCredentialSettings() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: gitCredentialKeys.detail,
    queryFn: getGitCredentials,
  });

  const [token, setToken] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  const saveMutation = useMutation({
    mutationFn: () => upsertGitCredentials({ token: token.trim() }),
    onSuccess: () => {
      setToken("");
      setFormError(null);
      void queryClient.invalidateQueries({
        queryKey: gitCredentialKeys.detail,
      });
    },
    onError: (e) => setFormError(errMsg(e, "保存失败，请重试")),
  });

  const clearMutation = useMutation({
    mutationFn: deleteGitCredentials,
    onSuccess: () => {
      setFormError(null);
      void queryClient.invalidateQueries({
        queryKey: gitCredentialKeys.detail,
      });
    },
    onError: (e) => setFormError(errMsg(e, "清除失败，请重试")),
    // A failure belongs on the page next to the form, not stranded in the modal.
    onSettled: () => setConfirmClear(false),
  });

  const configured = data?.configured === true;
  const busy = saveMutation.isPending || clearMutation.isPending;

  return (
    <div>
      <SettingsHeader
        title="Git 凭据"
        description="云端私有仓库用账户 Token。公网仓不用配。"
      />
      <SettingsStack>
        <Card className="space-y-4 p-4">
          <SettingsAsync
            loading={isLoading}
            error={isError ? errMsg(error, "无法加载凭据状态") : undefined}
          >
            <p className="text-sm text-muted-foreground">
              {configured
                ? `已配置 · ${data?.masked_token ?? "••••"} · 已加密保存`
                : "尚未配置"}
            </p>
          </SettingsAsync>

          <SettingField
            label="Token"
            htmlFor="git-pat"
            hint="GitHub 勾选 repo 权限即可"
          >
            <Input
              id="git-pat"
              type="password"
              autoComplete="off"
              placeholder={configured ? "输入新 Token 以替换" : "ghp_…"}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              disabled={busy}
            />
          </SettingField>

          <SettingsFormMessage>{formError}</SettingsFormMessage>

          <div className="flex flex-wrap gap-2">
            <Button
              disabled={busy || !token.trim()}
              icon={
                saveMutation.isPending ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : undefined
              }
              onClick={() => saveMutation.mutate()}
            >
              {configured ? "更新凭据" : "保存凭据"}
            </Button>
            {configured && (
              <Button
                variant="neutral"
                disabled={busy}
                onClick={() => setConfirmClear(true)}
              >
                清除
              </Button>
            )}
          </div>
        </Card>
      </SettingsStack>

      <ConfirmDialog
        open={confirmClear}
        onOpenChange={setConfirmClear}
        title="清除账户 Git 凭据？"
        description="云私仓的 clone / push 将失败，直至重新配置 Token。"
        confirmLabel="清除"
        tone="danger"
        busy={clearMutation.isPending}
        onConfirm={() => clearMutation.mutate()}
      />
    </div>
  );
}
