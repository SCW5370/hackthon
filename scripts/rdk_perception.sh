#!/usr/bin/env bash
set -eo pipefail

SAFEEXEC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${SAFEEXEC_ROOT}/.run"
mkdir -p "${RUN_DIR}"

source /opt/tros/humble/setup.bash
set -u
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export FASTRTPS_DEFAULT_PROFILES_FILE=/opt/tros/humble/lib/hobot_shm/config/shm_fastdds.xml
export RMW_FASTRTPS_USE_QOS_FROM_XML=1
export ROS_DISABLE_LOANED_MESSAGES=1

start_process() {
  local name="$1"
  shift
  local pid_file="${RUN_DIR}/${name}.pid"
  if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" 2>/dev/null; then
    echo "${name} already running"
    return
  fi
  nohup setsid "$@" >"${RUN_DIR}/${name}.log" 2>&1 &
  echo $! >"${pid_file}"
  echo "started ${name} pid=$(cat "${pid_file}")"
}

stop_process() {
  local name="$1"
  local pid_file="${RUN_DIR}/${name}.pid"
  if [[ -f "${pid_file}" ]]; then
    local pid
    pid="$(cat "${pid_file}")"
    kill -- "-${pid}" 2>/dev/null || kill "${pid}" 2>/dev/null || true
    rm -f "${pid_file}"
  fi
}

case "${1:-status}" in
  start)
    start_process camera \
      ros2 launch hobot_usb_cam hobot_usb_cam.launch.py \
      usb_video_device:=/dev/video0 \
      usb_image_width:=640 \
      usb_image_height:=480 \
      usb_pixel_format:=mjpeg \
      usb_zero_copy:=False
    sleep 2
    start_process codec \
      ros2 launch hobot_codec hobot_codec_decode.launch.py \
      codec_in_mode:=ros \
      codec_out_mode:=shared_mem \
      codec_sub_topic:=/image \
      codec_pub_topic:=/hbmem_img
    sleep 2
    start_process body \
      bash -lc "cd /opt/tros/humble/lib/mono2d_body_detection && \
      source /opt/tros/humble/setup.bash && \
      export RMW_IMPLEMENTATION=rmw_fastrtps_cpp && \
      export FASTRTPS_DEFAULT_PROFILES_FILE=/opt/tros/humble/lib/hobot_shm/config/shm_fastdds.xml && \
      export RMW_FASTRTPS_USE_QOS_FROM_XML=1 && \
      export ROS_DISABLE_LOANED_MESSAGES=1 && \
      exec ros2 run mono2d_body_detection mono2d_body_detection \
      --ros-args --log-level warn \
      -p model_file_name:=config/multitask_body_head_face_hand_kps_960x544.hbm \
      -p model_type:=0 \
      -p is_shared_mem_sub:=1 \
      -p ai_msg_pub_topic_name:=/hobot_mono2d_body_detection"
    sleep 2
    start_process bridge \
      env PYTHONPATH="${SAFEEXEC_ROOT}:${PYTHONPATH:-}" \
      python3 "${SAFEEXEC_ROOT}/adapters/rdk_fact_bridge.py" \
      --config "${SAFEEXEC_ROOT}/config/demo.json"
    ;;
  stop)
    stop_process bridge
    stop_process body
    stop_process codec
    stop_process camera
    echo "SafeExec RDK perception stopped"
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  status)
    for name in camera codec body bridge; do
      pid_file="${RUN_DIR}/${name}.pid"
      if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" 2>/dev/null; then
        echo "${name}: running pid=$(cat "${pid_file}")"
      else
        echo "${name}: stopped"
      fi
    done
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
