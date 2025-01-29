from scytheautoupdate import check_for_updates
import subprocess
import sys

if False:
    if check_for_updates():
        print("Changes have been made to main.py. Restarting the program...")
        subprocess.run([sys.executable, "main.py"])
        sys.exit()

import cv2
from constants import (
    DisplayConstants,
    CameraConstants,
    ObjectDetectionConstants,
    NetworkTableConstants,
)
from detector import detect
import numpy as np
from point_rotation import rotate2d
from time import time, sleep
from threading import Thread, Lock

from networktables import NetworkTables

# ANSI color codes
RED = "\033[91m"
GREEN = "\033[92m"
RESET = "\033[0m"

if DisplayConstants.render_output:
    from sapphirerenderer import SapphireRenderer, ParticleManager

    renderer = SapphireRenderer(draw_axis=True)

    note_object = renderer.add_object(
        "Torus", args=(np.array([0.0, 0.0, 0.0]), (255, 135, 0), 1.5, 0.5, 5)
    )

    particle_manager = ParticleManager(note_object, renderer)

if DisplayConstants.run_web_server:
    from web_server import VideoStreamer

    video_streamer = VideoStreamer()

# As a client to connect to a robot
NetworkTables.initialize(server=NetworkTableConstants.server_address)

sd = NetworkTables.getTable("SmartDashboard")

running = True
ready_count = 0

detection_data = {}
lock = Lock()

image_data = {}


def print_available_cameras():
    for i in range(10):  # Check up to camera index 9 (adjust if needed)
        cap = cv2.VideoCapture(i)
        if not cap.isOpened():
            print(RED + f"Camera index {i} is not available." + RESET)
        else:
            print(GREEN + f"Camera index {i} is available." + RESET)
            cap.release()


def convert_pixels_to_degrees(x, y, width_angle_per_pixel, height_angle_per_pixel):
    """
    Converts the given pixel coordinates to degrees
    :param x: the x coordinate (width) from the center of the image
    :param y: the y coordinate (height) from the center of the image
    :param width_angle_per_pixel: the angle per pixel in the width
    :param height_angle_per_pixel: the angle per pixel in the height
    :return: (x, y) in degrees
    """
    x_angle = width_angle_per_pixel * (ObjectDetectionConstants.input_size / 2 - x)
    y_angle = height_angle_per_pixel * (ObjectDetectionConstants.input_size / 2 - y)

    return -x_angle, y_angle


def calculate_local_note_position(
    x_angle, y_angle, camera_offset_pos, camera_h_angle, camera_v_angle
):
    """
    Calculates the position of the note in the local coordinate system
    :param x_angle: the x angle in degrees
    :param y_angle: the y angle in degrees
    :param camera_offset_pos: the offset of the camera from the center of the robot
    :param camera_h_angle: the angle of the camera from directly forward, positive is right
    :param camera_v_angle: the angle of the camera from directly forward, positive is up
    :return: the position of the note in the local coordinate system
    """
    x_angle += camera_h_angle
    y_angle += camera_v_angle

    # soh cah toa
    x_position = np.tan(np.radians(90 + y_angle)) * camera_offset_pos[2]

    return rotate2d((x_position, 0), np.radians(x_angle))


def convert_to_global_position(
    local_position, robot_position, robot_angle, camera_offset_pos
):
    """
    Converts the local position to the global position
    :param local_position: the local position of the note
    :param robot_position: the position of the robot
    :param robot_angle: the angle of the robot
    :param camera_offset_pos: the offset of the camera from the center of the robot
    :return: the global position of the note
    """
    return (
        np.array(rotate2d(local_position, robot_angle)) + robot_position
    ) - np.array([camera_offset_pos[0], camera_offset_pos[1]])


def camera_thread(camera_data):
    cap = cv2.VideoCapture(camera_data["camera_id"])
    global image_data

    if not cap.isOpened():
        print(f"\nCould not open video device {camera_data['camera_id']}\n")
        print("Available cameras:")
        print("-" * 50)
        print_available_cameras()
        print("-" * 50)
        raise ImportError("Could not open video device")

    while True:
        _, frame = cap.read()

        image_data[camera_data["name"]] = frame

        # cv2.imshow('frame1', frame)

        # cv2.waitKey(1)

        sleep(0.05)


def calculation_thread(camera_data):
    print(f"Starting thread for {camera_data['name']}")

    # pre-calculate values
    width_angle_per_pixel = (
        camera_data["camera_width_angle"] / ObjectDetectionConstants.input_size
    )
    height_angle_per_pixel = (
        camera_data["camera_height_angle"] / ObjectDetectionConstants.input_size
    )

    global running, ready_count, image_data

    with lock:
        ready_count += 1

    try:
        while running:
            start_time = time()
            # Capture frame-by-frame
            # _, frame = cap.read()

            frame = image_data[camera_data["name"]]

            if DisplayConstants.show_output:
                cv2.imshow("frame2", frame)

            cv2.waitKey(1)

            if DisplayConstants.show_output:
                detection, frame = detect(frame, verbose=False, return_image=True)
            else:
                detection = detect(frame, verbose=False, return_image=False)

            global detection_data

            for result in detection:
                boxes = result.boxes

                note_data = []

                for box in boxes:
                    x1, y1, x2, y2 = (
                        int(box.xyxy[0][0].item()),
                        int(box.xyxy[0][1].item()),
                        int(box.xyxy[0][2].item()),
                        int(box.xyxy[0][3].item()),
                    )

                    # get center of the box
                    center_x = (x1 + x2) / 2
                    center_y = (y1 + y2) / 2

                    # convert the center to degrees
                    x_angle, y_angle = convert_pixels_to_degrees(
                        center_x,
                        center_y,
                        width_angle_per_pixel,
                        height_angle_per_pixel,
                    )

                    # calculate the local position of the note
                    local_position = calculate_local_note_position(
                        x_angle,
                        y_angle,
                        camera_data["camera_offset_pos"],
                        camera_data["camera_h_angle"],
                        camera_data["camera_v_angle"],
                    )

                    robot_position = sd.getString(
                        key="wpilib estimated pose w/ ll",
                        defaultValue="Pose X: 0 Pose Y: 0 Rotation: 0",
                    )

                    # remove all characters that are not numbers or a space
                    robot_position = "".join(
                        filter(lambda x: x.isdigit() or x == " ", robot_position)
                    ).split(" ")

                    # remove all instances of a string with a space
                    robot_position = list(filter(lambda x: x != "", robot_position))

                    if DisplayConstants.debug:
                        print(f"Robot position: {robot_position}")

                    pose_x = float(robot_position[0])
                    pose_y = float(robot_position[1])
                    pose_angle = float(robot_position[2])

                    # convert the local position to the global position
                    global_position = convert_to_global_position(
                        local_position,
                        np.array([pose_x, pose_y]),
                        pose_angle,
                        camera_data["camera_offset_pos"],
                    )

                    # distance between robot position and note position
                    distance = np.linalg.norm(
                        np.array([pose_x, pose_y]) - global_position
                    )

                    if DisplayConstants.debug:
                        print("-" * 25 + f"thread for {camera_data['name']}" + "-" * 25)
                        print(f"global_position: {global_position}\n")
                        print(
                            f"x_angle: {round(x_angle, 2)}, y_angle: {round(y_angle, 2)}"
                        )
                        print(f"Robot position: {pose_x}, {pose_y}, {pose_angle}")

                    note_dict = {
                        "x": global_position[0],
                        "y": global_position[1],
                        "yaw": x_angle,
                        "distance": distance,
                    }
                    note_data.append(note_dict)

                with lock:
                    detection_data[camera_data["name"]] = note_data
                    if DisplayConstants.debug:
                        print(f"detection_data: {detection_data}")

            if DisplayConstants.show_output:
                # Display the resulting frame
                cv2.imshow("frame", frame)
                video_streamer.update_image(frame)

                cv2.waitKey(1)

                if DisplayConstants.debug:
                    print(f"Total frame time: {(time() - start_time) * 1000}ms\n")
                    print(f"Est fps: {1 / (time() - start_time)}\n")

                    print("-" * 60)

    except KeyboardInterrupt:
        print("Keyboard interrupt")
        raise KeyboardInterrupt


def main():
    try:
        for camera in CameraConstants.camera_list:
            t = Thread(target=camera_thread, args=(camera,))
            t.start()

            sleep(5)

            t = Thread(target=calculation_thread, args=(camera,))
            t.start()

        while ready_count < len(CameraConstants.camera_list):
            sleep(0.1)

        sleep(2)

        while True:
            global_list = []
            for camera in detection_data.keys():
                for note in detection_data[camera]:
                    global_list.append(note)

            if len(global_list) == 0:
                sd.putValue("notes", ["None"])
                sleep(0.1)
                continue

            # go through each note in global_list and combine notes that are close to each other
            combined_list = []
            for note in global_list:
                if len(combined_list) == 0:
                    combined_list.append(note)
                else:
                    for combined_note in combined_list:
                        if (
                            np.linalg.norm(
                                [
                                    combined_note["x"] - note["x"],
                                    combined_note["y"] - note["y"],
                                ]
                            )
                            < ObjectDetectionConstants.note_combined_threshold
                        ):
                            combined_note.append(note)
                            break
                    else:
                        combined_list.append(note)

            # sort the combined list by distance
            combined_list.sort(key=lambda x: x["distance"])

            if DisplayConstants.render_output:
                # change the amount of particles to the amount of notes by adding or removing particles
                particle_diff = len(combined_list) - len(particle_manager.particles)

                if particle_diff > 0:
                    for _ in range(particle_diff):
                        particle_manager.add_particle()
                elif particle_diff < 0:
                    for _ in range(-particle_diff):
                        particle_manager.remove_particle(particle_manager.particles[0])

                # move the particles to the correct position
                for i, note in enumerate(combined_list):
                    particle_manager.particles[i].move_absolute(
                        [note["x"] / 10, note["y"] / 10, 0]
                    )

            # convert the dicts of notes into strings
            combined_list = [str(note) for note in combined_list]

            if len(combined_list) == 0:
                combined_list = "None"

            if DisplayConstants.debug:
                print(f"Combined list: {combined_list}")
            sd.putValue("notes", combined_list)

            sleep(0.1)

    except KeyboardInterrupt:
        print("Keyboard interrupt")

        cv2.destroyAllWindows()

        global running
        running = False

        exit(0)


if __name__ == "__main__":
    main()
