/** "Red" underlined in red: the product's name is its mark. */
export function Wordmark({ size = 'small' }: { size?: 'small' | 'large' }) {
  return (
    <span className={`mark mark-${size}`}>
      R<span className="mark-red">ed</span>line
    </span>
  )
}
