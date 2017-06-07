from __future__ import absolute_import
from __future__ import division 
from __future__ import print_function

import sys, os, pdb

import numpy as np
import utils.dgm as dgm 

import tensorflow as tf
from tensorflow.contrib.tensorboard.plugins import projector


""" Generative models for labels with stochastic inputs: P(Z)P(X|Z)P(Y|X,Z) """

class igssl:
   
    def __init__(self, Z_DIM=2, LEARNING_RATE=0.005, NUM_HIDDEN=[4], ALPHA=0.1, TYPE_PX='Gaussian', NONLINEARITY=tf.nn.relu, 
                 LABELED_BATCH_SIZE=16, UNLABELED_BATCH_SIZE=128, NUM_EPOCHS=75, Z_SAMPLES=1, BINARIZE=False, verbose=1, logging=True):
    	## Step 1: define the placeholders for input and output
    	self.Z_DIM = Z_DIM                                   # stochastic inputs dimension       
    	self.NUM_HIDDEN = NUM_HIDDEN                         # (list) number of hidden layers/neurons per network
    	self.NONLINEARITY = NONLINEARITY		     # activation functions	
 	self.TYPE_PX = TYPE_PX				     # Distribution of X (Gaussian, Bernoulli)	
    	self.lr = LEARNING_RATE 			     # learning rate
    	self.LABELED_BATCH_SIZE = LABELED_BATCH_SIZE         # labeled batch size 
	self.UNLABELED_BATCH_SIZE = UNLABELED_BATCH_SIZE     # labeled batch size 
    	self.alpha = ALPHA 				     # weighting for additional term
    	self.Z_SAMPLES = Z_SAMPLES 			     # number of monte-carlo samples
    	self.NUM_EPOCHS = NUM_EPOCHS                         # training epochs
	self.BINARIZE = BINARIZE                             # sample inputs from Bernoulli distribution if true 
    	self.verbose = verbose				     # control output: 0-ELBO, 1-accuracy, 2-Q-accuracy
	self.LOGGING = logging                               # use tensorflow logging
    

    def fit(self, Data):
    	self._process_data(Data)
    	
	self._create_placeholders() 
        self._initialize_networks()
        
	## define loss function
	self._compute_loss_weights()
        L_l = tf.reduce_sum(self._labeled_loss(self.x_labeled, self.labels))
        L_u = tf.reduce_sum(self._unlabeled_loss(self.x_unlabeled))
        L_e = self._qxy_loss(self.x_labeled, self.labels)
        self.loss = -tf.add_n([self.labeled_weight*L_l , self.unlabeled_weight*L_u , self.alpha*L_e], name='loss')
        
        ## define optimizer
	varList = tf.trainable_variables()
	if False:
	    varList = [V for V in varList if 'Pz_x' in V.name or 'Q' in V.name]
      
        self.optimizer = tf.train.AdamOptimizer(self.lr).minimize(self.loss, var_list=varList)
	
	## compute accuracies
	train_acc = self.compute_acc(self.x_train, self.y_train)
	test_acc = self.compute_acc(self.x_test, self.y_test)

	## create summaries for tracking
	with tf.name_scope("summaries_elbo"):
	    tf.summary.scalar("ELBO", self.loss)
	    tf.summary.scalar("Labeled Loss", L_l)
	    tf.summary.scalar("Unlabeled Loss", L_u)
	    tf.summary.scalar("Additional Penalty", L_e)	    	
	    self.summary_op_elbo = tf.summary.merge_all()


	with tf.name_scope("summaries_accuracy"):
	    train_summary = tf.summary.scalar("Train Accuracy", train_acc)
	    test_summary = tf.summary.scalar("Test Accuracy", test_acc)
	    self.summary_op_acc = tf.summary.merge([train_summary, test_summary])


        ## initialize session and train
        SKIP_STEP, epoch, step = 50, 0, 0
	with tf.Session() as sess:
            sess.run(tf.global_variables_initializer())
            total_loss, l_l, l_u, l_e = 0.0, 0.0, 0.0, 0.0
	    saver = tf.train.Saver()
	    if self.LOGGING:
                writer = tf.summary.FileWriter(self.LOGDIR, sess.graph)
            while epoch < self.NUM_EPOCHS:
                x_labeled, labels, x_unlabeled, _ = Data.next_batch(self.LABELED_BATCH_SIZE, self.UNLABELED_BATCH_SIZE)
		if self.BINARIZE == True:
	 	    x_labeled, x_unlabeled = self._binarize(x_labeled), self._binarize(x_unlabeled)
            	_, loss_batch, l_lb, l_ub, l_eb, summary_elbo = sess.run([self.optimizer, self.loss, L_l, L_u, L_e, self.summary_op_elbo], 
            			     	     		    feed_dict={self.x_labeled: x_labeled, 
		           		    	 		       self.labels: labels,
		  	            		     		       self.x_unlabeled: x_unlabeled})
                #pdb.set_trace()
            	if self.LOGGING:
             	    writer.add_summary(summary_elbo, global_step=step)

		total_loss, l_l, l_u, l_e, step = total_loss+loss_batch, l_l+l_lb, l_u+l_ub, l_e+l_eb, step+1
                if Data._epochs_unlabeled > epoch:
        	    saver.save(sess, self.ckpt_dir, global_step=step+1)
		    if self.verbose==0:
		        acc_train, acc_test, summary_acc = sess.run([train_acc, test_acc, self.summary_op_acc],
						         feed_dict = {self.x_train:Data.data['x_train'],
		  	            		     		       self.y_train:Data.data['y_train'],
		  	            		     		       self.x_test:Data.data['x_test'],
		  	            		     		       self.y_test:Data.data['y_test']})
		    	self._hook_loss(epoch, step, total_loss, l_l, l_u, l_e, acc_train, acc_test)
        	        total_loss, l_l, l_u, l_e, step = 0.0, 0.0, 0.0, 0.0, 0

			if self.LOGGING:
         	            writer.add_summary(summary_acc, global_step=epoch)
		    
		    elif self.verbose==2:
		        acc_train, acc_test,  = sess.run([train_acc_q, test_acc_q],
						         feed_dict = {self.x_train:x_train,
						     	              self.y_train:Data.data['y_train'],
								      self.x_test:x_test,
								      self.y_test:Data.data['y_test']})
		        print('At epoch {}: Training: {:5.3f}, Test: {:5.3f}'.format(epoch, acc_train, acc_test))
		    epoch += 1
   	    if self.LOGGING:
	        writer.close()


    def predict_new(self, x, n_iters=100):
	predictions = self.predict(x, n_iters)
	saver = tf.train.Saver()
	with tf.Session() as session:
            ckpt = tf.train.get_checkpoint_state(self.ckpt_dir)
            saver.restore(session, ckpt.model_checkpoint_path)
            preds = session.run([predictions])
	return preds[0]



    def predict(self, x, n_iters=100):
	z_, _ = dgm._forward_pass_Gauss(x, self.Qx_z, self.NUM_HIDDEN, self.NONLINEARITY)
	h = tf.concat([x, z_], axis=1)
	return dgm._forward_pass_Cat(h, self.Pzx_y, self.NUM_HIDDEN, self.NONLINEARITY)



    def _sample_xy(self, n_samples=int(1e3)):
	saver = tf.train.Saver()
	with tf.Session() as session:
            ckpt = tf.train.get_checkpoint_state(self.ckpt_dir)
            saver.restore(session, ckpt.model_checkpoint_path)
            z_ = np.random.normal(size=(n_samples, self.Z_DIM)).astype('float32')
            if self.TYPE_PX=='Gaussian':
                x_ = dgm._forward_pass_Gauss(z_, self.Pz_x, self.NUM_HIDDEN, self.NONLINEARITY)
            else:
            	x_ = dgm._forward_pass_Bernoulli(z_, self.Pz_x, self.NUM_HIDDEN, self.NONLINEARITY)
            h = tf.concat([x_[0],z_], axis=1)
            y_ = dgm._forward_pass_Cat(h, self.Pzx_y, self.NUM_HIDDEN, self.NONLINEARITY)
            x,y = session.run([x_,y_])
        return x[0],y

    def _sample_Z(self, x, n_samples):
	""" Sample from Z with the reparamterization trick """
	mean, log_var = dgm._forward_pass_Gauss(x, self.Qx_z, self.NUM_HIDDEN, self.NONLINEARITY)
	eps = tf.random_normal([n_samples, self.Z_DIM], 0, 1, dtype=tf.float32)
	return mean, log_var, tf.add(mean, tf.multiply(tf.sqrt(tf.nn.softplus(log_var)), eps))


    def _labeled_loss(self, x, y, z=None, q_mean=None, q_logvar=None):
	""" Compute necessary terms for labeled loss (per data point) """
	if z==None:
	    q_mean, q_logvar, z = self._sample_Z(x, self.Z_SAMPLES)
        logpx = self._compute_logpx(x, z)
	logpy = self._compute_logpy(y, x, z)
	klz = dgm._gauss_kl(q_mean, tf.nn.softplus(q_logvar))
	return logpx + logpy - klz


    def _unlabeled_loss(self, x):
	""" Compute necessary terms for unlabeled loss (per data point) """
	weights = dgm._forward_pass_Cat(x, self.Qx_y, self.NUM_HIDDEN, self.NONLINEARITY)
	q_mean, q_log_var, z = self._sample_Z(x, self.Z_SAMPLES)
	EL_l = 0 
	for i in range(self.NUM_CLASSES):
	    y = self._generate_class(i, x.get_shape()[0])
	    EL_l += tf.multiply(weights[:,i], self._labeled_loss(x, y, z, q_mean, q_log_var))
	ent_qy = -tf.reduce_sum(tf.multiply(weights, tf.log(1e-10 + weights)), axis=1)
	return tf.add(EL_l, ent_qy)


    def _qxy_loss(self, x, y):
	y_ = dgm._forward_pass_Cat_logits(x, self.Qx_y, self.NUM_HIDDEN, self.NONLINEARITY)
	return -tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(labels=y, logits=y_))


    def _compute_logpx(self, x, z):
	""" compute the likelihood of every element in x under p(x|z) """
	if self.TYPE_PX == 'Gaussian':
	    mean, log_var = dgm._forward_pass_Gauss(z,self.Pz_x, self.NUM_HIDDEN, self.NONLINEARITY)
	    mvn = tf.contrib.distributions.MultivariateNormalDiag(loc=mean, scale_diag=tf.nn.softplus(log_var))
	    return mvn.log_prob(x)
	elif self.TYPE_PX == 'Bernoulli':
	    pi = dgm._forward_pass_Bernoulli(z, self.Pz_x, self.NUM_HIDDEN, self.NONLINEARITY)
	    return tf.reduce_sum(tf.add(x * tf.log(1e-10 + pi),  (1-x) * tf.log(1e-10 + 1 - pi)), axis=1)


    def _compute_logpy(self, y, x, z):
	""" compute the likelihood of every element in y under p(y|x,z) """
	h = tf.concat([x,z], axis=1)
	y_ = dgm._forward_pass_Cat_logits(h, self.Pzx_y, self.NUM_HIDDEN, self.NONLINEARITY)
	return -tf.nn.softmax_cross_entropy_with_logits(labels=y, logits=y_)

    
    def _compute_loss_weights(self):
    	""" Compute scaling weights for the loss function """
        #self.labeled_weight = tf.cast(tf.divide(self.TRAINING_SIZE, tf.multiply(self.NUM_LABELED, self.LABELED_BATCH_SIZE)), tf.float32)
        #self.unlabeled_weight = tf.cast(tf.divide(self.TRAINING_SIZE, tf.multiply(self.NUM_UNLABELED, self.UNLABELED_BATCH_SIZE)), tf.float32)
        self.labeled_weight = tf.cast(self.TRAINING_SIZE / self.LABELED_BATCH_SIZE, tf.float32)
        self.unlabeled_weight = tf.cast(self.TRAINING_SIZE / self.UNLABELED_BATCH_SIZE, tf.float32)

    
    def compute_acc(self, x, y):
	y_ = self.predict(x)
	return tf.reduce_mean(tf.cast(tf.equal(tf.argmax(y_,axis=1), tf.argmax(y, axis=1)), tf.float32))

    def _binarize(self, x):
	return np.random.binomial(1, x)


    def _process_data(self, data):
    	""" Extract relevant information from data_gen """
    	self.N = data.N
    	self.TRAINING_SIZE = data.TRAIN_SIZE   	       # training set size
	self.TEST_SIZE = data.TEST_SIZE                # test set size
	self.NUM_LABELED = data.NUM_LABELED    	       # number of labeled instances
	self.NUM_UNLABELED = data.NUM_UNLABELED        # number of unlabeled instances
	self.X_DIM = data.INPUT_DIM            	       # input dimension     
	self.NUM_CLASSES = data.NUM_CLASSES            # number of classes
	self.alpha = self.alpha * self.NUM_LABELED     # weighting for additional term
	self.data_name = data.NAME                     # dataset being used   
	self._allocate_directory()                     # logging directory     


    def _create_placeholders(self):
 	""" Create input/output placeholders """
	self.x_labeled = tf.placeholder(tf.float32, shape=[self.LABELED_BATCH_SIZE, self.X_DIM], name='labeled_input')
    	self.x_unlabeled = tf.placeholder(tf.float32, shape=[self.UNLABELED_BATCH_SIZE, self.X_DIM], name='unlabeled_input')
    	self.labels = tf.placeholder(tf.float32, shape=[self.LABELED_BATCH_SIZE, self.NUM_CLASSES], name='labels')
	self.x_train = tf.placeholder(tf.float32, shape=[self.TRAINING_SIZE, self.X_DIM], name='x_train')
	self.y_train = tf.placeholder(tf.float32, shape=[self.TRAINING_SIZE, self.NUM_CLASSES], name='y_train')
	self.x_test = tf.placeholder(tf.float32, shape=[self.TEST_SIZE, self.X_DIM], name='x_test')
	self.y_test = tf.placeholder(tf.float32, shape=[self.TEST_SIZE, self.NUM_CLASSES], name='y_test')
    	


    def _initialize_networks(self):
    	""" Initialize all model networks """
	if self.TYPE_PX == 'Gaussian':
      	    self.Pz_x = dgm._init_Gauss_net(self.Z_DIM, self.NUM_HIDDEN, self.X_DIM, 'Pz_x_')
	elif self.TYPE_PX == 'Bernoulli':
	    self.Pz_x = dgm._init_Cat_net(self.Z_DIM, self.NUM_HIDDEN, self.X_DIM, 'Pz_x_')
    	self.Pzx_y = dgm._init_Cat_net(self.Z_DIM+self.X_DIM, self.NUM_HIDDEN, self.NUM_CLASSES, 'Pzx_y_')
    	self.Qx_z = dgm._init_Gauss_net(self.X_DIM, self.NUM_HIDDEN, self.Z_DIM, 'Qx_z_')
    	self.Qx_y = dgm._init_Cat_net(self.X_DIM, self.NUM_HIDDEN, self.NUM_CLASSES, 'Qx_y_')

    

    def _generate_class(self, k, num):
	""" create one-hot encoding of class k with length num """
	y = np.zeros(shape=(num, self.NUM_CLASSES))
	y[:,k] = 1
	return tf.constant(y, dtype=tf.float32)


    def _hook_loss(self, epoch, SKIP_STEP, total_loss, l_l, l_u, l_e, acc_train, acc_test):
    	print("Epoch {}: Total:{:5.1f}, Labeled:{:5.1f}, unlabeled:{:5.1f}, " 
              "Additional:{:5.1f}, Training: {:5.3f}, Test: {:5.3f}".format(epoch, 
									    total_loss/SKIP_STEP,l_l/SKIP_STEP,
									    l_u/SKIP_STEP,l_e/SKIP_STEP,
									    acc_train, acc_test))

     
    def _allocate_directory(self):
	self.LOGDIR = 'graphs/gssl2-'+self.data_name+'-'+str(self.lr)+'-'+str(self.NUM_LABELED)+'/'
	self.ckpt_dir = './ckpt/gssl2-'+self.data_name+'-'+str(self.lr)+'-'+str(self.NUM_LABELED) + '/'
        if not os.path.isdir(self.ckpt_dir):
	    os.mkdir(self.ckpt_dir)