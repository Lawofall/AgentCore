import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { AuthGate } from "./components/auth/AuthGate";
import { PushBridge } from "./components/layout/PushBridge";
import { Toaster } from "./components/ui/Toaster";
import { TooltipProvider } from "./components/ui/tooltip";
import { queryClient } from "./lib/queryClient";
import { router } from "./router";

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={300}>
        <PushBridge />
        <AuthGate>
          <RouterProvider router={router} />
        </AuthGate>
        {/* Toast host lives outside the router so any screen (incl. the auth gate
            and the route-error fallback) can surface a toast. */}
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
