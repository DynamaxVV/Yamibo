export function LoadingSpinner({ className = '' }: { className?: string }) {
  return <span aria-hidden="true" className={`inline-block h-3 w-3 shrink-0 rounded-full border-2 border-yamibo-burgundy border-t-transparent animate-spin ${className}`} />
}

export function LoadingIndicator({ label, className = '' }: { label: string; className?: string }) {
  return (
    <div className={`panel flex items-center justify-center gap-2 py-4 text-xs font-mono text-muted-foreground ${className}`} role="status">
      <LoadingSpinner />
      <span>{label}</span>
    </div>
  )
}
