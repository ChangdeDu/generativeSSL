from __future__ import absolute_import
from __future__ import division 
from __future__ import print_function

import numpy as np

import utils.dgm as dgm
import tensorflow as tf
from tensorflow.contrib.tensorboard.plugins import projector

import pdb, os

""" Standard VAE: P(Z)P(X|Z) """

class VAE:
    def __init__(self, Z_DIM=2, NUM_HIDDEN=[4,4], LEARNING_RATE=0.005, NONLINEARITY=tf.nn.relu,
    		 BATCH_SIZE=16,NUM_EPOCHS=75, Z_SAMPLES=1, TYPE_PX='Gaussian', BINARIZE=False, LOGGING=False):

        self.Z_DIM = Z_DIM                                   # stochastic layer dimension       
	self.TYPE_PX = TYPE_PX                               # input likelihood
	self.BINARIZE = BINARIZE                             # binarize the data or not
    	self.NUM_HIDDEN = NUM_HIDDEN                         # number of hidden layers per network
    	self.NONLINEARITY = NONLINEARITY		     # activation functions	
    	self.lr = LEARNING_RATE 			     # learning rate
	self.BATCH_SIZE = BATCH_SIZE                         # batch size 
    	self.Z_SAMPLES = Z_SAMPLES 			     # number of monte-carlo samples
    	self.NUM_EPOCHS = NUM_EPOCHS                         # training epochs
	self.LOGGING = LOGGING                               # whether to log into TensorBoard

    def fit(self, Data):
    	self._process_data(Data)

    	# define placeholders for input output
    	self._create_placeholders()
    	# define weights and initialize networks
    	self._initialize_networks()
    	# define the loss function
    	self.loss = -self._compute_ELBO(self.x_batch)
	test_elbo = self._compute_ELBO(self.x_test)
	train_elbo = self._compute_ELBO(self.x_train)
    	# define optimizer
    	self.optimizer = tf.train.AdamOptimizer(self.lr).minimize(self.loss)
	# summary statistics
	with tf.name_scope("summaries_elbo"):
	    tf.summary.scalar("ELBO", self.loss)
	    tf.summary.scalar("Train Loss", train_elbo)
	    tf.summary.scalar("Test Loss", test_elbo)
	    self.summary_op = tf.summary.merge_all()

    	# run and train
    	epoch, step = 0, 0
    	with tf.Session() as sess:
    	    sess.run(tf.global_variables_initializer()) 
    	    total_loss = 0
	    saver = tf.train.Saver()
	    if self.LOGGING:
    	        writer = tf.summary.FileWriter(self.LOGDIR, sess.graph)

    	    while epoch < self.NUM_EPOCHS:
    	    	x_batch, _ = Data.next_batch_regular(self.BATCH_SIZE)
		if self.BINARIZE:
		    x_batch = self._binarize(x)
	        feed_dict = {self.x_batch:x_batch, self.x_train:Data.data['x_train'], self.x_test:Data.data['x_test']}
    	    	_, loss_batch, summary = sess.run([self.optimizer, self.loss, self.summary_op], feed_dict=feed_dict)
		
		if self.LOGGING:
		    writer.add_summary(summary, global_step=step)
    	    	total_loss += loss_batch
    	    	step = step + 1 

    	    	if Data._epochs_regular > epoch:
		    saver.save(sess, self.ckpt_dir, global_step=step)
		    trainELBO, testELBO = sess.run([train_elbo, test_elbo], feed_dict=feed_dict)
    	    	    print('Epoch: {}, Train ELBO: {:5.3f}, Test ELBO: {:5.3f}'.format(epoch, trainELBO, testELBO))
    	    	    total_loss, step, epoch = 0.0, 0, epoch + 1
	    
	    if self.LOGGING:
      	        writer.close()

    
    def _encode(self, x):
    	mean, log_var = dgm._forward_pass_Gauss(x, self.Qx_z, self.NUM_HIDDEN, self.NONLINEARITY)
    	return mean

    def _decode(self, z):
	if self.TYPE_PX=='Gaussian':
    	    mean, log_var = dgm._forward_pass_Gauss(z, self.Pz_x, self.NUM_HIDDEN, self.NONLINEARITY)
	elif self.TYPE_PX=='Bernoulli':
	    mean = dgm._forward_pass_Bernoulli(z, self.Pz_x, self.NUM_HIDDEN, self.NONLINEARITY)
    	return mean

    def _sample_Z(self, x, n_samples=1):
    	""" Sample from Z with the reparamterization trick """
	mean, log_var = dgm._forward_pass_Gauss(x, self.Qx_z, self.NUM_HIDDEN, self.NONLINEARITY)
	eps = tf.random_normal([tf.shape(x)[0], self.Z_DIM], dtype=tf.float32)
	return mean, log_var, mean + tf.nn.softplus(log_var) * eps 

    def _compute_ELBO(self, x):
    	z_mean, z_log_var, z = self._sample_Z(x)
    	KLz = dgm._gauss_kl(z_mean, tf.nn.softplus(z_log_var))
    	logpx = self._log_x_z(x, z)
	total_elbo = logpx - KLz
        return tf.reduce_sum(total_elbo)


    def _generate_data(self, n_samps=int(1e3)):
	saver = tf.train.Saver()
  	with tf.Session() as session:
	    ckpt = tf.train.get_checkpoint_state(self.ckpt_dir)
	    saver.restore(session, ckpt.model_checkpoint_path)
	    z_ = np.random.normal(size=(n_samps, self.Z_DIM)).astype('float32')
	    if self.TYPE_PX=='Gaussian':
                x_, _ = dgm._forward_pass_Gauss(z_, self.Pz_x, self.NUM_HIDDEN, self.NONLINEARITY)
            else:
                x_ = dgm._forward_pass_Bernoulli(z_, self.Pz_x, self.NUM_HIDDEN, self.NONLINEARITY) 
	    x_ = session.run(x_)
	return x_
    
    def _log_x_z(self, x, z):
    	""" compute the likelihood of every element in x under p(x|z) """
        if self.TYPE_PX=='Gaussian':
	    mean, log_var = dgm._forward_pass_Gauss(z, self.Pz_x, self.NUM_HIDDEN, self.NONLINEARITY)
	    mvn = tf.contrib.distributions.MultivariateNormalDiag(loc=mean, scale_diag=tf.nn.softplus(log_var)) 
	    return mvn.log_prob(x)
	elif self.TYPE_PX == 'Bernoulli':
            pi = dgm._forward_pass_Bernoulli(z, self.Pz_x, self.NUM_HIDDEN, self.NONLINEARITY)
            return tf.reduce_sum(tf.add(x * tf.log(1e-10 + pi),  (1-x) * tf.log(1e-10 + 1 - pi)), axis=1)


    def _initialize_networks(self):
	if self.TYPE_PX=='Gaussian':
    	    self.Pz_x = dgm._init_Gauss_net(self.Z_DIM, self.NUM_HIDDEN, self.X_DIM, 'Pz_x')
	elif self.TYPE_PX=='Bernoulli':
    	    self.Pz_x = dgm._init_Cat_net(self.Z_DIM, self.NUM_HIDDEN, self.X_DIM, 'Pz_x')	   
    	self.Qx_z = dgm._init_Gauss_net(self.X_DIM, self.NUM_HIDDEN, self.Z_DIM, 'Qx_z')

    
    def _binarize(self, x):
	return np.random.binomial(1,x)


    def _process_data(self, data):
    	""" Extract relevant information from data_gen """
    	self.dataset = data.NAME                                 # name of dataset
    	self.N = data.N                                          # number of examples
    	self.TRAINING_SIZE = data.TRAIN_SIZE   			 # training set size
	self.TEST_SIZE = data.TEST_SIZE                          # test set size
	self.X_DIM = data.INPUT_DIM            			 # input dimension     
	self.NUM_CLASSES = data.NUM_CLASSES                      # number of classes
    	self._allocate_directory()                               # logging directory
	

    def _create_placeholders(self):
    	self.x_train = tf.placeholder(tf.float32, shape=[self.TRAINING_SIZE, self.X_DIM], name='x_train')
    	self.y_train = tf.placeholder(tf.float32, shape=[self.TRAINING_SIZE, self.NUM_CLASSES], name='y_train')
    	self.x_test = tf.placeholder(tf.float32, shape=[self.TEST_SIZE, self.X_DIM], name='x_test')
    	self.y_test = tf.placeholder(tf.float32, shape=[self.TEST_SIZE, self.NUM_CLASSES], name='y_test')
    	self.x_batch = tf.placeholder(tf.float32, shape=[self.BATCH_SIZE, self.X_DIM], name='x_batch')
    	self.y_batch = tf.placeholder(tf.float32, shape=[self.BATCH_SIZE, self.NUM_CLASSES], name='y_batch')

    def _allocate_directory(self):
	self.LOGDIR = 'graphs/VAE-'+self.dataset+'-'+str(self.lr)+'/'
        self.ckpt_dir = './ckpt/VAE-'+self.dataset+'-'+str(self.lr)+'/'
        if not os.path.isdir(self.ckpt_dir):
            os.mkdir(self.ckpt_dir)
