import { Badge } from "@/ui/primitives/badge";
import { Button } from "@/ui/primitives/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/primitives/card";
import { PageHeader } from "@/ui/page-header";
import { PageShell } from "@/ui/page-shell";
import { StatusBadge } from "@/ui/status";

export function App() {
  return (
    <main className="mx-auto min-h-dvh max-w-5xl">
      <PageShell gap="loose">
        <PageHeader
          kicker="Agent Factory · Session 3.1"
          title="Dashboard primitives"
          description="A small test page for the shared theme and copied UI components."
          actions={<Button>Primary action</Button>}
        />
        <Card>
          <CardHeader>
            <CardTitle>Theme and component check</CardTitle>
            <CardDescription>Shared srt-gateway styles with factory status vocabulary.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-3">
            <Button>Default</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="outline">Outline</Button>
            <Badge variant="live">Signal</Badge>
            <Badge variant="warn">Warning</Badge>
            <StatusBadge state="running" />
            <StatusBadge state="needs-you" />
            <StatusBadge state="approved" />
          </CardContent>
        </Card>
      </PageShell>
    </main>
  );
}
