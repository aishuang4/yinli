/** 登录页品牌区：用工作成果场景与轨道图形表达引力的 AI 办公定位。 */

import { ArrowUpRight, Check, FileText, Layers3, Sparkles, Table2 } from 'lucide-react';

export function AuthBrandPanel() {
  return (
    <aside className="auth-brand" aria-label="引力 AI 办公工作台">
      <div className="auth-brand__identity">
        <img src="./favicon.svg" alt="" width={42} height={42} />
        <span>引力 <span className="auth-brand__wordmark">YINLI</span></span>
      </div>
      <div className="auth-brand__content">
        <p className="auth-brand__eyebrow"><span /> 为你的工作，注入引力</p>
        <h2>让想法，<br />成为<span>作品。</span></h2>
        <p className="auth-brand__description">从灵感到成果，让 AI 与你一起完成。<br />文档、表格与项目，都在一个工作台。</p>
        <div className="auth-scene" role="img" aria-label="工作场景示意：AI 整理项目资料，生成方案文档和数据表格">
          <div className="auth-scene__orbit auth-scene__orbit--outer" />
          <div className="auth-scene__orbit auth-scene__orbit--inner" />
          <div className="auth-scene__spark auth-scene__spark--one" />
          <div className="auth-scene__spark auth-scene__spark--two" />
          <div className="auth-scene__request">
            <span className="auth-scene__request-icon"><Layers3 size={18} /></span>
            <span>把这些资料，整理成一份项目方案</span>
            <ArrowUpRight size={16} />
          </div>
          <div className="auth-scene__result">
            <div className="auth-scene__result-heading">
              <span className="auth-scene__assistant"><Sparkles size={17} /></span>
              <span>让每一步，都走向成果<small>整理资料 · 提炼重点 · 生成文件</small></span>
              <span className="auth-scene__done"><Check size={12} /> 已完成</span>
            </div>
            <div className="auth-scene__files">
              <div className="auth-scene__file">
                <span className="auth-scene__file-icon"><FileText size={22} /></span>
                <span>项目方案<small>结构清晰，即刻可用</small></span>
                <span className="auth-scene__file-type">DOCX</span>
              </div>
              <div className="auth-scene__file">
                <span className="auth-scene__file-icon auth-scene__file-icon--sheet"><Table2 size={22} /></span>
                <span>数据分析表<small>信息汇总，一目了然</small></span>
                <span className="auth-scene__file-type">XLSX</span>
              </div>
            </div>
          </div>
          <span className="auth-scene__caption">工作场景示意</span>
        </div>
      </div>
      <footer className="auth-brand__footer"><span>你的 AI 办公工作台</span><span>想法有引力，工作有回响。</span></footer>
    </aside>
  );
}
