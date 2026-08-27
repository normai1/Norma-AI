"use client";

import { Button, Card, PageShell } from "@/components/organizations/ui";

export default function AppError({ reset }: { error: Error; reset: () => void }) {
  return (
    <PageShell title="Something went wrong">
      <Card>
        <p className="text-slate-300">
          Something went wrong on our end. Try again, and if it keeps
          happening, let us know what you were doing.
        </p>

        <div className="mt-4">
          <Button onClick={() => reset()}>Try again</Button>
        </div>
      </Card>
    </PageShell>
  );
}
