/** 把 Ant Design App message 绑定到全局反馈工具。 */

import { App as AntdApp } from 'antd';
import { useEffect } from 'react';
import { bindFeedbackMessage } from '../utils/feedback';

export function FeedbackBridge() {
  const { message } = AntdApp.useApp();

  useEffect(() => {
    bindFeedbackMessage(message);
  }, [message]);

  return null;
}
