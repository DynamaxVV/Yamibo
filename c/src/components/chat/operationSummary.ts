/** Display the exact requested scope without exposing protocol details first. */
export function operationSummary(tool: unknown, value: unknown, lang: 'zh' | 'en' = 'zh'): string {
  const args = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const tx = (zh: string, en: string) => lang === 'en' ? en : zh
  if (tool === 'create_jobs' || tool === 'authorize_job_plan') {
    const actions = lang === 'en' ? { archive: 'Archive', update: 'Update', export: 'Export' } : { archive: '归档', update: '更新', export: '导出' }
    const action = actions[String(args.action) as keyof typeof actions] || String(args.action || (lang === 'en' ? 'Process' : '处理'))
    return tx(`${action} ${Array.isArray(args.tids) ? args.tids.length : 0} 个帖子。批准仅对这份计划生效。`, `${action} ${Array.isArray(args.tids) ? args.tids.length : 0} threads. Approval applies only to this plan.`)
  }
  if (tool === 'update_agent_guidance') return tx('修改助手的固定业务指南。请核对下方的新内容；这不会改变程序权限。', 'Update the assistant guidance. Review the new text below; this does not change application permissions.')
  if (tool === 'delete_work_file') return tx('将指定工作文件移入回收区。请核对文件标识与版本。', 'Move the specified work file to the recycle area. Check its ID and version.')
  if (tool === 'update_work_file') return tx('更新指定工作文件，并保留原版本。', 'Update the specified work file and keep its previous version.')
  if (tool === 'create_work_file') return tx(`保存工作文件：${String(args.name || '')}`, `Save work file: ${String(args.name || '')}`)
  return tx('请核对本次操作的范围与参数。', 'Review the scope and parameters of this operation.')
}
