import { useLlmProviders } from "@/hooks/useLlmProviders";
import { Navigate } from "react-router-dom";

/**
 * `/more` index 落点：还没接上模型 → 服务商；已经能聊 → 用量。
 * 装配在工具箱，窄屏从设置里的「装配」进。
 */
export function MoreIndexRedirect() {
  const { data, isLoading, isError } = useLlmProviders();

  if (isLoading) return null;
  if (isError || !data) {
    return <Navigate to="/more/usage" replace />;
  }

  const hasProviders = data.providers.length > 0;
  const hasPlatform = data.platform_available;
  if (hasPlatform || hasProviders) {
    return <Navigate to="/more/usage" replace />;
  }
  return <Navigate to="/more/providers" replace />;
}
