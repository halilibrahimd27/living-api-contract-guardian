import Link from "next/link"
import { listCampaigns } from "@/api/client"
import type { CampaignRead, CampaignState } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

async function getCampaigns(): Promise<CampaignRead[]> {
  try {
    return await listCampaigns()
  } catch {
    return []
  }
}

type BadgeVariant = "default" | "destructive" | "warning" | "success" | "secondary"

const stateVariant: Record<CampaignState, BadgeVariant> = {
  draft: "secondary",
  active: "default",
  decaying: "warning",
  ready_to_remove: "destructive",
  completed: "success",
  aborted: "secondary",
}

const stateLabel: Record<CampaignState, string> = {
  draft: "Draft",
  active: "Active",
  decaying: "Decaying",
  ready_to_remove: "Ready to Remove",
  completed: "Completed",
  aborted: "Aborted",
}

export default async function CampaignsPage() {
  const campaigns = await getCampaigns()

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">Deprecation Campaigns</h1>
        <p className="mt-1 text-sm text-gray-500">
          Track the lifecycle of endpoint and field deprecation campaigns.
        </p>
      </div>

      {campaigns.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-gray-500">No campaigns yet.</p>
            <p className="mt-2 text-sm text-gray-400">
              Use{" "}
              <code className="rounded bg-gray-100 px-1 py-0.5 text-xs">POST /campaigns</code> to
              start your first deprecation campaign.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {campaigns.map((campaign) => (
            <Link key={campaign.id} href={`/campaigns/${campaign.id}`}>
              <Card className="cursor-pointer transition-shadow hover:shadow-md">
                <CardHeader>
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle className="truncate text-base">{campaign.name}</CardTitle>
                    <Badge
                      variant={stateVariant[campaign.state]}
                      className="shrink-0"
                    >
                      {stateLabel[campaign.state]}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  {campaign.description && (
                    <p className="mb-3 text-sm text-gray-600 line-clamp-2">
                      {campaign.description}
                    </p>
                  )}
                  <dl className="space-y-1 text-xs text-gray-500">
                    <div className="flex justify-between">
                      <dt>Threshold</dt>
                      <dd>{campaign.usage_threshold_pct}%</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt>Decay window</dt>
                      <dd>{campaign.decay_window_days}d</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt>Remaining clients</dt>
                      <dd>{campaign.remaining_clients.length}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt>Metric points</dt>
                      <dd>{campaign.decay_curve.length}</dd>
                    </div>
                  </dl>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
