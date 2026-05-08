import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Orçamento Automático',
  description: 'Sistema de gestão e automação de orçamentos',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  )
}
