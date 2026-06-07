"use client"

import type { MetricPoint } from "@/api/types"
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

interface DecayChartProps {
  data: MetricPoint[]
}

interface ChartDataPoint {
  date: string
  usage_count: number
  ewma_value: number
  remaining_clients: number
}

export function DecayChart({ data }: DecayChartProps) {
  if (data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-gray-400">
        No decay data yet
      </div>
    )
  }

  const chartData: ChartDataPoint[] = data.map((point) => ({
    date: new Date(point.sampled_at).toLocaleDateString(),
    usage_count: point.usage_count,
    ewma_value: Math.round(point.ewma_value * 100) / 100,
    remaining_clients: point.remaining_client_count,
  }))

  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={chartData} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
        <XAxis dataKey="date" tick={{ fontSize: 12 }} />
        <YAxis tick={{ fontSize: 12 }} />
        <Tooltip
          contentStyle={{ fontSize: 12, borderRadius: "6px" }}
          labelStyle={{ fontWeight: "bold" }}
        />
        <Line
          type="monotone"
          dataKey="usage_count"
          name="Usage Count"
          stroke="#3b82f6"
          strokeWidth={2}
          dot={{ r: 3 }}
          activeDot={{ r: 5 }}
        />
        <Line
          type="monotone"
          dataKey="ewma_value"
          name="EWMA"
          stroke="#10b981"
          strokeWidth={2}
          strokeDasharray="5 5"
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="remaining_clients"
          name="Remaining Clients"
          stroke="#f59e0b"
          strokeWidth={2}
          dot={{ r: 3 }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
