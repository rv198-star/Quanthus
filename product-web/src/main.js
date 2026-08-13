import Vue from 'vue'
import 'ant-design-vue/dist/antd.css'
import Alert from 'ant-design-vue/es/alert'
import Button from 'ant-design-vue/es/button'
import Dropdown from 'ant-design-vue/es/dropdown'
import Empty from 'ant-design-vue/es/empty'
import FormModel from 'ant-design-vue/es/form-model'
import Icon from 'ant-design-vue/es/icon'
import Input from 'ant-design-vue/es/input'
import Menu from 'ant-design-vue/es/menu'
import Modal from 'ant-design-vue/es/modal'
import message from 'ant-design-vue/es/message'
import Select from 'ant-design-vue/es/select'
import Tooltip from 'ant-design-vue/es/tooltip'
import App from './App.vue'
import router from './router'
import './styles.css'
import { installAntdRequireShim } from './shims/antd-require'

installAntdRequireShim()
;[Alert, Button, Dropdown, Empty, FormModel, Icon, Input, Menu, Modal, Select, Tooltip].forEach(component => Vue.use(component))
Vue.prototype.$message = message
Vue.config.productionTip = false

new Vue({ router, render: h => h(App) }).$mount('#app')
