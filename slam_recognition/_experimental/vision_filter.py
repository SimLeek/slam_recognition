import tensorflow as tf
from cvpubsubs import webcam_pub as w
from cvpubsubs.window_sub import SubscriberWindows
import math as m

from slam_recognition.util import zoom
from slam_recognition.constant_convolutions.center_surround import rgby_3, midget_rgc
from slam_recognition.util.zoom.to_image_list import zoom_tensor_to_image_list
from slam_recognition.constant_convolutions.edge_orientation_detector import rgb_2d_stripe_tensors
from slam_recognition.constant_convolutions.oriented_end_detector import rgb_2d_end_tensors
import numpy as np
from slam_recognition.util.regulator import regulate_tensor
from slam_recognition.constant_convolutions.gaussian_blur.gaussian_blur import blur_tensor


class VisionFilter(object):

    def __init__(self):
        """

        Current: Uses techniques from SLAM to detect specific types of features, using static tensors. Detects line ends
         right now.

        Future: Selects the brightest detected features, after an optional regulation input, and reorients/rescales the
         image around those, for each feature, pasting the results on top of each other. Can be used for recognition of
         multiple objects in a scene at the same time, regardless of position, orientation, or scale.
        """
        self.rgc = midget_rgc(2)
        self.rgby = rgby_3(2)
        self.simplex_boundaries_b = rgb_2d_stripe_tensors()
        self.blur = blur_tensor(2, lengths=7)
        self.simplex_end_stop = rgb_2d_end_tensors()

    def spatial_color_2d(self, pyramid_tensor):
        """ WARNING: EXPERIMENTAL

        Current: Applyes all the necessary convolutions onto an image to get the desired features. Gets line ends.

        Future: Please break this up into multiple functions and rename.

        :param pyramid_tensor: Input image pyramid, where images are same pixel size but different scales.
        :return: Image or tensor with detected features
        """
        # from: http://thegrimm.net/2017/12/14/tensorflow-image-convolution-edge-detection/

        tf.reset_default_graph()

        input_placeholder = tf.placeholder(
            dtype=tf.float32, shape=(pyramid_tensor.shape))

        with tf.name_scope('center_surround') and tf.device('/device:GPU:0'):  # Use CPU for small filters like these
            conv_rgc = tf.constant(self.rgc, dtype=tf.float32, shape=(3, 3, 3, 3))

            conv_rgby = tf.constant(self.rgby, dtype=tf.float32, shape=(3, 3, 3, 3))

            output_rgc = tf.maximum(
                tf.nn.conv2d(input=input_placeholder, filter=conv_rgc, strides=[1, 1, 1, 1], padding='SAME'),
                [0]
            )

            output_rgby = tf.maximum(tf.nn.conv2d(input=output_rgc, filter=conv_rgby, strides=[1, 1, 1, 1],
                                                  padding='SAME'), [0])
            simplex_orientation_filter_b = tf.constant(self.simplex_boundaries_b, dtype=tf.float32, shape=(3, 3, 3, 3))

            output_orient = tf.maximum(
                tf.nn.conv2d(
                    input=output_rgby, filter=simplex_orientation_filter_b, strides=[1, 1, 1, 1], padding='SAME'),
                [0]
            )

            conv_blur = tf.constant(self.blur, dtype=tf.float32, shape=(7, 7, 3, 3))

            output_orient = regulate_tensor(output_orient, conv_blur, 1.0, .1)

            simplex_end_filter = tf.constant(self.simplex_end_stop, dtype=tf.float32, shape=(7, 7, 3, 3))

            output_orient = tf.minimum(tf.maximum(
                tf.nn.conv2d(
                    input=output_orient, filter=simplex_end_filter, strides=[1, 1, 1, 1], padding='SAME'),
                [0]
            ),[255])

            conv_blur = tf.constant(self.blur, dtype=tf.float32, shape=(7, 7, 3, 3))

            output_orient = regulate_tensor(output_orient, conv_blur, 1.0, 1.0)

            #local maxima: https://stackoverflow.com/a/43801558/782170
            max_pooled_in_tensor = tf.nn.pool(output_orient, window_shape=(3, 3), pooling_type='MAX', padding='SAME')
            maxima = output_orient * tf.where(tf.equal(output_orient, max_pooled_in_tensor), output_orient, tf.zeros_like(output_orient))

            #todo: get this pseudocode working
            '''
            #get indices: https://stackoverflow.com/a/39223400/782170
            zero = tf.constant(0, dtype=tf.float32)
            where = tf.not_equal(maxima, zero)
            indices = tf.where(where)
            indices = sort(indices, key=lambda ind: maxima[ind])

            feature_layer = np.zeros_like(maxima)
            for i in indices:
                # todo: handle multi image scaling
                img_trans = tf.contrib.image.translate(
                    maxima, indices[i]-np.shape(maxima)[:2], "NEAREST"
                )
                img_rot = tf.contrib.image.rotate(
                    img_trans, get_angle(maxima[indices[i]]), "NEAREST"
                )
                feature_layer += img_rot
                if fill_amount(feature_layer) > 0.03:
                    break
                '''

        with tf.Session() as session:
            result_edge = session.run(maxima, feed_dict={
                input_placeholder: pyramid_tensor[:, :, :, :]})

        return result_edge

    def full_callback(self, frame, cam_id):
        """ Runs feature detection on an entire image, instead of a pyramid. Useful for debugging.

        :param frame: Input frame.
        :param cam_id: Unused.
        :return: Frame to display
        """
        frame1 = frame[:]
        npFrame = np.asarray([frame], dtype=np.float32) / 256.0
        frame = self.spatial_color_2d(npFrame)
        return frame.append(frame1)

    def pyramid_callback(self, frame, cam_id):
        """ Transforms the input frame into an image pyramid, then performs feature detection.

        :param frame: Input frame.
        :param cam_id: Unused.
        :return: Frame to display.
        """
        npFrame = np.asarray(frame, dtype=np.float32)
        frames = zoom_tensor_to_image_list(
            self.spatial_color_2d(zoom.from_image(npFrame, 3, [257, 144], m.e ** .5)))
        frame = frames[0]
        for f in range(1, len(frames)):
            frame = np.concatenate((frame, frames[f]), axis=1)
        return [frame]


if __name__ == '__main__':
    """MOVE TO UNIT TESTS ONCE IMPLEMENTATION IS FINISHED."""
    retina = VisionFilter()

    t = w.VideoHandlerThread(0, [retina.pyramid_callback] + w.display_callbacks,
                             request_size=(1280, 720),
                             high_speed=True,
                             fps_limit=240
                             )

    t.start()

    SubscriberWindows(window_names=["scale " + str(i) for i in range(6)] + ['1'],
                      video_sources=[0]
                      ).loop()

    w.CamCtrl.stop_cam(0)

    t.join()
