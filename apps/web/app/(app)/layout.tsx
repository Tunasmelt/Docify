import { WorkspaceProvider } from "@/components/layout/workspace-provider";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <WorkspaceProvider>{children}</WorkspaceProvider>;
}
