import Link from "next/link"
import { notFound } from "next/navigation"
import { getDiff } from "@/api/client"
import type { ChangeRecord, ChangeReport } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface PageProps {
  params: { id: string }
}

async function fetchDiff(id: string): Promise<ChangeReport | null> {
  try {
    return await getDiff(id)
  } catch {
    return null
  }
}

type VerdictVariant = "destructive" | "warning" | "success"

const verdictVariant: Record<string, VerdictVariant> = {
  breaking: "destructive",
  behavioral: "warning",
  additive: "success",
}

function ClientImpactMatrix({ changes }: { changes: ChangeRecord[] }) {
  const clientSet = new Set<string>()
  for (const c of changes) {
    for (const client of c.affected_clients) {
      clientSet.add(client)
    }
  }
  const clients = Array.from(clientSet).sort()

  if (clients.length === 0) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-gray-400">
          No client impact data available for this diff.
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-200 text-sm">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-4 py-3 text-left font-medium text-gray-700">Client</th>
            <th className="px-4 py-3 text-left font-medium text-gray-700">Breaking</th>
            <th className="px-4 py-3 text-left font-medium text-gray-700">Behavioral</th>
            <th className="px-4 py-3 text-left font-medium text-gray-700">Additive</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 bg-white">
          {clients.map((client) => {
            const clientChanges = changes.filter((c) => c.affected_clients.includes(client))
            const breaking = clientChanges.filter((c) => c.verdict === "breaking").length
            const behavioral = clientChanges.filter((c) => c.verdict === "behavioral").length
            const additive = clientChanges.filter((c) => c.verdict === "additive").length
            return (
              <tr key={client}>
                <td className="px-4 py-3 font-mono text-xs text-gray-900">{client}</td>
                <td className="px-4 py-3">
                  {breaking > 0 ? (
                    <Badge variant="destructive">{breaking}</Badge>
                  ) : (
                    <span className="text-gray-300">–</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {behavioral > 0 ? (
                    <Badge variant="warning">{behavioral}</Badge>
                  ) : (
                    <span className="text-gray-300">–</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {additive > 0 ? (
                    <Badge variant="success">{additive}</Badge>
                  ) : (
                    <span className="text-gray-300">–</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default async function DiffDetailPage({ params }: PageProps) {
  const report = await fetchDiff(params.id)

  if (!report) {
    notFound()
  }

  return (
    <div>
      <div className="mb-6">
        <Link href="/" className="text-sm text-blue-600 hover:underline">
          ← Services
        </Link>
      </div>

      <div className="mb-8">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-gray-900">Diff Report</h1>
          <Badge variant="secondary">{report.contract_kind}</Badge>
        </div>
        <p className="mt-1 font-mono text-xs text-gray-400">
          {params.id}
        </p>
      </div>

      {/* Summary cards */}
      <div className="mb-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card>
          <CardContent className="pt-6 text-center">
            <p className="text-3xl font-bold text-gray-900">{report.summary.total}</p>
            <p className="mt-1 text-sm text-gray-500">Total</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6 text-center">
            <p className="text-3xl font-bold text-red-600">{report.summary.breaking}</p>
            <p className="mt-1 text-sm text-gray-500">Breaking</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6 text-center">
            <p className="text-3xl font-bold text-yellow-600">{report.summary.behavioral}</p>
            <p className="mt-1 text-sm text-gray-500">Behavioral</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6 text-center">
            <p className="text-3xl font-bold text-green-600">{report.summary.additive}</p>
            <p className="mt-1 text-sm text-gray-500">Additive</p>
          </CardContent>
        </Card>
      </div>

      {/* Client impact matrix */}
      <div className="mb-8">
        <h2 className="mb-4 text-lg font-semibold text-gray-800">Client Impact Matrix</h2>
        <Card>
          <ClientImpactMatrix changes={report.changes} />
        </Card>
      </div>

      {/* Change list */}
      <div>
        <h2 className="mb-4 text-lg font-semibold text-gray-800">
          Changes ({report.changes.length})
        </h2>
        {report.changes.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center text-sm text-gray-400">
              No changes detected.
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            {report.changes.map((change) => (
              <Card key={change.change_id}>
                <CardHeader>
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <Badge variant={verdictVariant[change.verdict] ?? "secondary"}>
                        {change.verdict}
                      </Badge>
                      <code className="truncate text-xs text-gray-600">{change.location}</code>
                    </div>
                    <span className="shrink-0 text-xs text-gray-400">{change.rule_id}</span>
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="text-sm text-gray-700">{change.rationale}</p>
                  {change.affected_clients.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1">
                      <span className="text-xs text-gray-400 mr-1">Affected:</span>
                      {change.affected_clients.map((client) => (
                        <Badge key={client} variant="secondary">
                          {client}
                        </Badge>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
