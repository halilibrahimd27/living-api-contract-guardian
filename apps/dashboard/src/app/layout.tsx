import type { Metadata } from "next"
import { Inter } from "next/font/google"
import Link from "next/link"
import { Providers } from "@/components/providers"
import "./globals.css"

const inter = Inter({ subsets: ["latin"] })

export const metadata: Metadata = {
  title: "Living API Contract Guardian",
  description: "Monitor API contracts, diffs, and deprecation campaigns",
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <Providers>
          <div className="min-h-screen bg-gray-50">
            <nav className="border-b border-gray-200 bg-white">
              <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
                <div className="flex h-16 items-center justify-between">
                  <div className="flex items-center gap-8">
                    <Link
                      href="/"
                      className="text-lg font-semibold text-gray-900 hover:text-blue-600"
                    >
                      API Guardian
                    </Link>
                    <div className="flex gap-6">
                      <Link
                        href="/"
                        className="text-sm text-gray-600 hover:text-gray-900"
                      >
                        Services
                      </Link>
                      <Link
                        href="/campaigns"
                        className="text-sm text-gray-600 hover:text-gray-900"
                      >
                        Campaigns
                      </Link>
                    </div>
                  </div>
                </div>
              </div>
            </nav>
            <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
              {children}
            </main>
          </div>
        </Providers>
      </body>
    </html>
  )
}
