/** Display the exact requested scope without exposing protocol details first. */
export function operationSummary(tool: unknown, value: unknown): string {
  const args = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  if (tool === 'create_jobs' || tool === 'authorize_job_plan') {
    const action = { archive: '归档', update: '更新', export: '导出' }[String(args.action)] || String(args.action || '处理')
    return `${action} ${Array.isArray(args.tids) ? args.tids.length : 0} 个帖子。批准仅对这份计划生效。`
  }
  if (tool === 'update_agent_guidance') return '修改助手的固定业务指南。请核对下方的新内容；这不会改变程序权限。'
  if (tool === 'delete_work_file') return '将指定工作文件移入回收区。请核对文件标识与版本。'
  if (tool === 'update_work_file') return '更新指定工作文件，并保留原版本。'
  if (tool === 'create_work_file') return `保存工作文件：${String(args.name || '')}`
  return '请核对本次操作的范围与参数。'
}
