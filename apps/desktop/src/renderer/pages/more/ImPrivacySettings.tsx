import { BlockedUsersDialog } from "@/components/messages/BlockedUsersDialog";
import {
  SettingRow,
  SettingsAsync,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import { PageHeader } from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import { errMsg } from "@/lib/errMsg";
import { notifyError } from "@/lib/toast";
import {
  type DirectorySettings,
  type WhoCanFriend,
  getDirectory,
  normalizeWhoCanDm,
  updateDirectory,
} from "@/services/messaging";
import { useCallback, useEffect, useState } from "react";

interface OptionRow<T extends string> {
  value: T;
  label: string;
  description: string;
}

const DM_OPTIONS: OptionRow<"anyone" | "friends">[] = [
  {
    value: "anyone",
    label: "任何人",
    description: "可被搜到的用户均可向你发起私信（陌生人首条会进入消息请求）。",
  },
  {
    value: "friends",
    label: "仅好友",
    description: "只有已同意的好友可以向你发起新私信。",
  },
];

const FRIEND_OPTIONS: OptionRow<WhoCanFriend>[] = [
  {
    value: "anyone",
    label: "任何人",
    description: "可被搜到的用户均可向你发送好友申请。",
  },
  {
    value: "group_members",
    label: "仅共同群成员",
    description: "须与你有共同群聊的用户才能申请加好友。",
  },
  {
    value: "nobody",
    label: "不允许任何人",
    description: "关闭好友申请入口（已有好友不受影响）。",
  },
];

/**
 * 消息隐私设置（/more/messages）— discoverable + who_can_friend + who_can_dm
 * + 拉黑列表入口（消息IM.md §九）。
 */
export function ImPrivacySettings() {
  const [settings, setSettings] = useState<DirectorySettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [blocksOpen, setBlocksOpen] = useState(false);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setSettings(await getDirectory());
    } catch (e) {
      setLoadError(errMsg(e, "加载消息隐私设置失败"));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const patch = async (next: Partial<DirectorySettings>) => {
    if (!settings) return;
    setPending(true);
    const prev = settings;
    setSettings({ ...settings, ...next });
    try {
      const saved = await updateDirectory(next);
      setSettings(saved);
    } catch (e) {
      setSettings(prev);
      notifyError(e, "保存失败");
    } finally {
      setPending(false);
    }
  };

  const whoCanDm = settings ? normalizeWhoCanDm(settings.who_can_dm) : null;

  return (
    <div>
      <PageHeader title="消息隐私" />

      <SettingsStack>
        {settings === null ? (
          <SettingsAsync
            variant="card"
            loading={loadError === null}
            error={loadError}
            onRetry={() => void load()}
          />
        ) : (
          <>
            <SettingRow
              align="start"
              label="可被搜索"
              description="关闭后，他人无法通过用户名或 ID 精确搜到你（已在群内的身份不受影响）。"
              control={
                <Switch
                  checked={settings.discoverable}
                  onCheckedChange={(discoverable) =>
                    void patch({ discoverable })
                  }
                  disabled={pending}
                  label="可被搜索"
                />
              }
            />

            <SettingsSection
              title="谁可以加我为好友"
              contentClassName="space-y-2"
            >
              {FRIEND_OPTIONS.map((option) => (
                <SettingRow
                  key={option.value}
                  variant="select"
                  align="start"
                  label={option.label}
                  description={option.description}
                  selected={settings.who_can_friend === option.value}
                  disabled={pending}
                  onClick={() => void patch({ who_can_friend: option.value })}
                />
              ))}
            </SettingsSection>

            <SettingsSection title="谁可以私信我" contentClassName="space-y-2">
              {DM_OPTIONS.map((option) => (
                <SettingRow
                  key={option.value}
                  variant="select"
                  align="start"
                  label={option.label}
                  description={option.description}
                  selected={whoCanDm === option.value}
                  disabled={pending}
                  onClick={() => void patch({ who_can_dm: option.value })}
                />
              ))}
            </SettingsSection>
          </>
        )}

        <SettingRow
          variant="nav"
          label="已拉黑"
          description="查看并管理拉黑列表"
          onClick={() => setBlocksOpen(true)}
        />
      </SettingsStack>

      <BlockedUsersDialog
        open={blocksOpen}
        onClose={() => setBlocksOpen(false)}
      />
    </div>
  );
}
