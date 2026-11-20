import { AppBar } from '../components/AppBar'

export function ErrorState({
  title,
  message,
  onRetry,
  onNewDeck,
}: {
  title: string
  message: string
  onRetry?: () => void
  onNewDeck: () => void
}) {
  return (
    <div className="page">
      <AppBar step={1} onHome={onNewDeck} />
      <main className="error-page" role="alert">
        <span className="error-icon" aria-hidden="true">!</span>
        <h1 className="h-page h-page-md">{title}</h1>
        <p className="lede">{message}</p>
        <div className="row">
          {onRetry && <button className="btn btn-primary" onClick={onRetry}>Try again</button>}
          <button className={`btn ${onRetry ? 'btn-secondary' : 'btn-primary'}`} onClick={onNewDeck}>Check another deck</button>
        </div>
      </main>
    </div>
  )
}
