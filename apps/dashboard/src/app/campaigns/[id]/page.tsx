"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { getCampaign } from "@/api/client"
import type { CampaignRead, CampaignState } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { DecayChart } from "@/components/decay-chart"

interface PageProps {
  params: { id: string }
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

function CampaignDetail({ campaign }: { campaign: CampaignRead }) {
  return (
    <div>
      <div className="mb-6">
        <Link href="/campaigns" className="text-sm text-blue-600 hover:underline">
          ← Campaigns
        </Link>
      </div>

      <div className="mb-8">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-gray-900">{campaign.name}</h1>
          <Badge variant={stateVariant[campaign.state]}>
            {stateLabel[campaign.state]}
          </Badge>
        </div>
        {campaign.description && (
          <p className="mt-2 text-sm text-gray-600">{campaign.description}</p>
        )}
        <p className="mt-1 font-mono text-xs text-gray-400">{campaign.id}</p>
      </div>

      {/* Metadata cards */}
      <div className="mb-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card>
          <CardContent className="pt-6 text-center">
            <p className="text-2xl font-bold text-gray-900">{campaign.peak_usage}</p>
            <p className="mt-1 text-xs text-gray-500">Peak Usage</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6 text-center">
            <p className="text-2xl font-bold text-gray-900">
              {campaign.usage_threshold_pct}%
            </p>
            <p className="mt-1 text-xs text-gray-500">Threshold</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6 text-center">
            <p className="text-2xl font-bold text-gray-900">{campaign.decay_window_days}d</p>
            <p className="mt-1 text-xs text-gray-500">Decay Window</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6 text-center">
            <p className="text-2xl font-bold text-gray-900">
              {campaign.remaining_clients.length}
            </p>
            <p className="mt-1 text-xs text-gray-500">Remaining Clients</p>
          </CardContent>
        </Card>
      </div>

      {/* Decay chart */}
      <div className="mb-8">
        <h2 className="mb-4 text-lg font-semibold text-gray-800">Decay Curve</h2>
        <Card>
          <CardContent className="pt-6">
            <DecayChart data={campaign.decay_curve} />
          </CardContent>
        </Card>
      </div>

      {/* Remaining clients */}
      {campaign.remaining_clients.length > 0 && (
        <div className="mb-8">
          <h2 className="mb-4 text-lg font-semibold text-gray-800">Remaining Clients</h2>
          <Card>
            <CardContent className="pt-6">
              <div className="flex flex-wrap gap-2">
                {campaign.remaining_clients.map((client) => (
                  <Badge key={client} variant="warning">
                    {client}
                  </Badge>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Reminder PRs */}
      {campaign.reminder_prs.length > 0 && (
        <div className="mb-8">
          <h2 className="mb-4 text-lg font-semibold text-gray-800">Reminder PRs</h2>
          <div className="space-y-2">
            {campaign.reminder_prs.map((pr) => (
              <Card key={pr.id}>
                <CardContent className="py-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-gray-900">{pr.client_repo}</p>
                      {pr.branch_name && (
                        <p className="text-xs text-gray-500">Branch: {pr.branch_name}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      {pr.pr_number !== null && (
                        <span className="text-xs text-gray-500">PR #{pr.pr_number}</span>
                      )}
                      <Badge
                        variant={
                          pr.pr_state === "open"
                            ? "default"
                            : pr.pr_state === "merged"
                              ? "success"
                              : "secondary"
                        }
                      >
                        {pr.pr_state}
                      </Badge>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* Campaign metadata */}
      <div>
        <h2 className="mb-4 text-lg font-semibold text-gray-800">Details</h2>
        <Card>
          <CardContent className="pt-6">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
              {campaign.endpoint_id && (
                <>
                  <dt className="text-gray-500">Endpoint ID</dt>
                  <dd className="col-span-1 truncate font-mono text-xs text-gray-700 sm:col-span-2">
                    {campaign.endpoint_id}
                  </dd>
                </>
              )}
              {campaign.field_path && (
                <>
                  <dt className="text-gray-500">Field path</dt>
                  <dd className="col-span-1 font-mono text-xs text-gray-700 sm:col-span-2">
                    {campaign.field_path}
                  </dd>
                </>
              )}
              {campaign.github_repo && (
                <>
                  <dt className="text-gray-500">GitHub repo</dt>
                  <dd className="col-span-1 text-gray-700 sm:col-span-2">
                    {campaign.github_repo}
                  </dd>
                </>
              )}
              <dt className="text-gray-500">Created</dt>
              <dd className="col-span-1 text-gray-700 sm:col-span-2">
                {new Date(campaign.created_at).toLocaleString()}
              </dd>
              <dt className="text-gray-500">Updated</dt>
              <dd className="col-span-1 text-gray-700 sm:col-span-2">
                {new Date(campaign.updated_at).toLocaleString()}
              </dd>
            </dl>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

export default function CampaignDetailPage({ params }: PageProps) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["campaign", params.id],
    queryFn: () => getCampaign(params.id),
    refetchInterval: 10_000,
  })

  if (isLoading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <p className="text-gray-500">Loading campaign…</p>
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="text-center">
          <p className="text-gray-700">Campaign not found or API unreachable.</p>
          <Link href="/campaigns" className="mt-2 block text-sm text-blue-600 hover:underline">
            ← Back to campaigns
          </Link>
        </div>
      </div>
    )
  }

  return <CampaignDetail campaign={data} />
}
