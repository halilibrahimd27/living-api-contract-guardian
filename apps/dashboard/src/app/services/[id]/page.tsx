import Link from "next/link"
import { notFound } from "next/navigation"
import { getService, getServiceContracts } from "@/api/client"
import type { ContractRead, ServiceRead } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface PageProps {
  params: { id: string }
}

async function fetchServiceData(
  id: string,
): Promise<{ service: ServiceRead; contracts: ContractRead[] } | null> {
  try {
    const [service, contracts] = await Promise.all([
      getService(id),
      getServiceContracts(id).catch(() => [] as ContractRead[]),
    ])
    return { service, contracts }
  } catch {
    return null
  }
}

const kindVariant: Record<string, "default" | "secondary"> = {
  openapi: "default",
  proto: "secondary",
}

export default async function ServiceDetailPage({ params }: PageProps) {
  const data = await fetchServiceData(params.id)

  if (!data) {
    notFound()
  }

  const { service, contracts } = data

  return (
    <div>
      <div className="mb-6">
        <Link href="/" className="text-sm text-blue-600 hover:underline">
          ← Services
        </Link>
      </div>

      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">{service.name}</h1>
        <div className="mt-2 flex items-center gap-4 text-sm text-gray-500">
          <span>Owner: {service.owner}</span>
          <span>
            Created:{" "}
            {new Date(service.created_at).toLocaleDateString(undefined, {
              year: "numeric",
              month: "long",
              day: "numeric",
            })}
          </span>
        </div>
        <p className="mt-1 text-xs text-gray-400">ID: {service.id}</p>
      </div>

      <div>
        <h2 className="mb-4 text-lg font-semibold text-gray-800">Contracts</h2>

        {contracts.length === 0 ? (
          <Card>
            <CardContent className="py-12 text-center">
              <p className="text-gray-500">No contracts uploaded yet.</p>
              <p className="mt-2 text-sm text-gray-400">
                Use{" "}
                <code className="rounded bg-gray-100 px-1 py-0.5 text-xs">
                  POST /services/{"{id}"}/contracts
                </code>{" "}
                to upload the first contract version.
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {contracts.map((contract) => (
              <Card key={contract.id}>
                <CardHeader>
                  <div className="flex items-start justify-between">
                    <CardTitle className="text-base">{contract.name}</CardTitle>
                    <Badge variant={kindVariant[contract.kind] ?? "secondary"}>
                      {contract.kind}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  <dl className="space-y-1 text-sm">
                    <div className="flex justify-between">
                      <dt className="text-gray-500">Version hash</dt>
                      <dd className="truncate font-mono text-xs text-gray-700 max-w-[180px]">
                        {contract.version.version_hash.slice(0, 16)}…
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-gray-500">Uploaded</dt>
                      <dd className="text-gray-700">
                        {new Date(contract.version.created_at).toLocaleDateString()}
                      </dd>
                    </div>
                  </dl>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* Client Impact Matrix placeholder */}
      <div className="mt-10">
        <h2 className="mb-4 text-lg font-semibold text-gray-800">
          Recent Diff — Client Impact
        </h2>
        <Card>
          <CardContent className="py-8 text-center text-sm text-gray-400">
            Run{" "}
            <code className="rounded bg-gray-100 px-1 py-0.5 text-xs">POST /diff</code>{" "}
            to generate a change report, then view it at{" "}
            <code className="rounded bg-gray-100 px-1 py-0.5 text-xs">
              /diff/{"<diff_id>"}
            </code>
            .
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
