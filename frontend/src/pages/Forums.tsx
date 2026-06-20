import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Forum } from '../api/client'
import { Badge, ContentBadge } from '../components/Badge'

export function Forums() {
  const [forums, setForums] = useState<Forum[]>([])
  useEffect(() => { api.forums().then(setForums) }, [])

  return (
    <div className="table-wrap"><table>
      <thead><tr><th>ID</th><th>名称</th><th>内容类型</th><th>贴子数</th><th>启用</th><th>操作</th></tr></thead>
      <tbody>
        {forums.map(f => (
          <tr key={f.forum_id}>
            <td className="mono">{f.forum_id}</td>
            <td>{f.name}</td>
            <td className="nowrap"><ContentBadge kind={f.content_kind} /></td>
            <td>{f.thread_count}</td>
            <td><Badge status={f.enabled ? 'ok' : 'muted'}>{f.enabled ? '是' : '否'}</Badge></td>
            <td>
              <Link to={`/threads?forum_id=${f.forum_id}`} className="btn-subtle" style={{ textDecoration: 'none' }}>
                查看贴子
              </Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table></div>
  )
}
