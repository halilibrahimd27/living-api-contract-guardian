import Link from "next/link"
import { listServices } from "@/api/client"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { ServiceRead } from "@/api/types"

async function getServices(): Promise<ServiceRead[]> {
  try {
    return await listServices()
  } catch {
    return []
  }
}

export default async function HomePage() {
  const services = await getServices()

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">Services</h1>
        <p className="mt-1 text-sm text-gray-500">
          All registered API services tracked by the Guardian.
        </p>
      </div>

      {services.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-gray-500">No services registered yet.</p>
            <p className="mt-2 text-sm text-gray-400">
              Use{" "}
              <code className="rounded bg-gray-100 px-1 py-0.5 text-xs">
                POST /services
              </code>{" "}
              to register your first service.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {services.map((service) => (
            <Link key={service.id} href={`/services/${service.id}`}>
              <Card className="cursor-pointer transition-shadow hover:shadow-md">
                <CardHeader>
                  <div className="flex items-start justify-between">
                    <CardTitle className="truncate text-base">{service.name}</CardTitle>
                    <Badge variant="secondary" className="ml-2 shrink-0">
                      {service.owner}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-gray-400">
                    Created{" "}
                    {new Date(service.created_at).toLocaleDateString(undefined, {
                      year: "numeric",
                      month: "short",
                      day: "numeric",
                    })}
                  </p>
                  <p className="mt-1 truncate text-xs text-gray-400">ID: {service.id}</p>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
